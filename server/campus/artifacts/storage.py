import hashlib
import os
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from ..errors import Problem

CHUNK_SIZE = 1024 * 1024


def safe_relative(path):
    if (
        not path
        or any(ord(c) < 32 for c in path)
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise Problem("UNSAFE_PATH", "Only portable relative paths are allowed", 422)
    return PurePosixPath(path)


def secret_path(path):
    for part in path.parts:
        lower = part.casefold()
        if (
            lower
            in {
                ".git",
                ".venv",
                "venv",
                ".ssh",
                ".aws",
                ".npmrc",
                ".netrc",
                "credentials.json",
                "id_rsa",
                "id_ed25519",
                "secrets.json",
            }
            or lower == ".env"
            or lower.startswith(".env.")
            or lower.endswith((".pem", ".key", ".p12", ".pfx"))
        ):
            return True
    return False


def validate_archive(path, max_bytes):
    names = set()
    directories = set()
    files = set()
    expanded = 0
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000:
                raise Problem("ARCHIVE_LIMIT", "Project contains too many files", 422)
            for entry in entries:
                name = safe_relative(entry.filename.removesuffix("/"))
                key = unicodedata.normalize("NFC", str(name)).casefold()
                parents = {
                    unicodedata.normalize("NFC", str(p)).casefold() for p in name.parents if p.parts
                }
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (
                    secret_path(name)
                    or key in names
                    or key in directories
                    and not entry.is_dir()
                    or parents & files
                    or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or entry.flag_bits & 1
                ):
                    raise Problem("UNSAFE_ARCHIVE", "Project contains a blocked archive entry", 422)
                names.add(key)
                directories.update(parents)
                if not entry.is_dir():
                    files.add(key)
                expanded += entry.file_size
                if expanded > max_bytes or entry.file_size > 200 * max(1, entry.compress_size):
                    raise Problem("ARCHIVE_LIMIT", "Project expansion exceeds its limit", 422)
            if archive.testzip() is not None:
                raise Problem("INVALID_ARCHIVE", "Project ZIP checksum does not match", 422)
    except (BadZipFile, NotImplementedError, RuntimeError, EOFError, ValueError) as exc:
        raise Problem("INVALID_ARCHIVE", "Project must be a valid supported ZIP archive", 422) from exc


def read_selected(path, selected):
    snippets = {}
    remaining = 24_000
    with ZipFile(path) as archive:
        for filename in selected:
            name = safe_relative(filename)
            if secret_path(name):
                raise Problem("BLOCKED_FILE", "Selected file cannot be shared", 422)
            try:
                entry = archive.getinfo(str(name))
                if entry.is_dir() or entry.file_size > min(8000, remaining):
                    raise Problem("FILE_LIMIT", "Selected text exceeds the inspection limit", 422)
                raw = archive.read(entry)
                snippets[filename] = raw.decode("utf-8")
                remaining -= len(raw)
            except (KeyError, UnicodeError) as exc:
                raise Problem(
                    "INVALID_FILE", "Selected path must be UTF-8 text in the project", 422
                ) from exc
    return snippets


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.blobs = self.root / "blobs"
        self.parts = self.root / "parts"
        for directory in (self.blobs, self.parts):
            directory.mkdir(parents=True, exist_ok=True)

    def blob(self, sha):
        return self.blobs / sha[:2] / sha

    def part(self, upload_id, number):
        return self.parts / str(upload_id) / str(number)

    @staticmethod
    def sync_directory(directory):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def publish(self, destination, chunks, sha, size, validate=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
        path = Path(temp)
        try:
            checksum = hashlib.sha256()
            length = 0
            with os.fdopen(fd, "wb") as file:
                for chunk in chunks:
                    length += len(chunk)
                    if length > size:
                        raise Problem("INVALID_SIZE", "Upload exceeds its declared size", 422)
                    checksum.update(chunk)
                    file.write(chunk)
                if length != size or checksum.hexdigest() != sha:
                    raise Problem("CHECKSUM_MISMATCH", "Upload size or SHA-256 does not match", 422)
                file.flush()
                os.fsync(file.fileno())
            if validate:
                validate(path)
            os.replace(path, destination)
            self.sync_directory(destination.parent)
            self.sync_directory(destination.parent.parent)
        except OSError as exc:
            raise Problem(
                "STORAGE_UNAVAILABLE", "Storage could not publish the file", 503, retryable=True
            ) from exc
        finally:
            path.unlink(missing_ok=True)

    def assemble(self, upload_id, count):
        for number in range(count):
            with self.part(upload_id, number).open("rb") as part:
                yield from iter(lambda: part.read(CHUNK_SIZE), b"")
