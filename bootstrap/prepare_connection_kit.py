"""Create a verified native connection kit from explicit, already cached inputs.

Operator tool: Python stdlib plus Docker. Recipients need only their native
shell/PowerShell, the bundled Go executable and Docker Desktop. No image build,
pull, push, secret output, or system installation is performed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import ctypes
import errno
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
from urllib.parse import urlsplit

CHUNK = 65536
IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[0-9a-f]{64}\Z")
LOCAL_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
TAG = re.compile(r"[a-z0-9][a-z0-9._:/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
PROFILE_FIELDS = ("id", "accelerator", "platform", "image_digest", "guard_version")
MAX_IMAGE = 8 << 30
MAX_PACKAGE = 16 << 30
MAX_RAW = 64 << 30


def _linked(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _no_links(path):
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if _linked(metadata):
            raise ValueError("package paths must not contain links or reparse points")


@contextmanager
def _regular(path, limit):
    _no_links(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("package input is not a bounded regular file")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            yield source
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK):
            value.update(chunk)
    return value.hexdigest()


def _source_sha(source):
    source.seek(0)
    value = hashlib.sha256()
    while chunk := source.read(CHUNK):
        value.update(chunk)
    source.seek(0)
    return value.hexdigest()


def _write(path, value, mode=0o600):
    with path.open("xb") as stream:
        stream.write(value.encode("utf-8") if isinstance(value, str) else value)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def _file_ref(root, path):
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path), "size_bytes": path.stat().st_size}


def _uvarint(stream):
    result = 0
    for shift in range(0, 64, 7):
        raw = stream.read(1)
        if not raw:
            raise ValueError("truncated Go build info")
        value = raw[0]
        result |= (value & 127) << shift
        if not value & 128:
            return result
    raise ValueError("invalid Go build-info length")


def _go_string(stream):
    length = _uvarint(stream)
    if length > 2 << 20:
        raise ValueError("Go build info exceeds bound")
    value = stream.read(length)
    if len(value) != length:
        raise ValueError("truncated Go build info")
    return value


def _binary(source, target):
    goos, arch = target.split("-")
    header = source.read(64)
    if goos == "darwin":
        if len(header) < 32 or struct.unpack("<I", header[:4])[0] != 0xFEEDFACF or struct.unpack("<I", header[4:8])[0] != 0x0100000C or struct.unpack("<I", header[12:16])[0] != 2:
            raise ValueError("runner must be a thin native Mach-O ARM64 executable")
    else:
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("runner must be a native Windows AMD64 executable")
        offset = struct.unpack("<I", header[60:64])[0]
        if offset > 1 << 20:
            raise ValueError("invalid PE header offset")
        source.seek(offset)
        pe = source.read(26)
        if len(pe) != 26 or pe[:4] != b"PE\0\0" or struct.unpack("<H", pe[4:6])[0] != 0x8664 or not struct.unpack("<H", pe[22:24])[0] & 2 or struct.unpack("<H", pe[24:26])[0] != 0x20B:
            raise ValueError("runner must be an executable PE32+ AMD64 binary")
    source.seek(0)
    tail, consumed, found = b"", 0, None
    while chunk := source.read(CHUNK):
        block = tail + chunk
        start = 0
        while True:
            position = block.find(b"\xff Go buildinf:", start)
            if position < 0:
                break
            absolute = consumed - len(tail) + position
            if absolute % 16 == 0:
                found = absolute
                break
            start = position + 1
        if found is not None:
            break
        consumed += len(chunk)
        tail = block[-15:]
    if found is None:
        raise ValueError("runner has no native Go build metadata")
    source.seek(found)
    info = source.read(32)
    if len(info) != 32 or info[14:16] != bytes([8, 2]):
        raise ValueError("runner requires modern inline 64-bit Go build metadata")
    version, module = _go_string(source), _go_string(source)
    if not re.fullmatch(rb"go1\.[0-9]+(?:\.[0-9]+)?(?:[a-zA-Z0-9.-]*)", version) or len(module) < 32:
        raise ValueError("runner has invalid Go build metadata")
    try:
        lines = module[16:-16].decode("utf-8").splitlines()
    except UnicodeError:
        raise ValueError("invalid Go build settings") from None
    settings = dict(line[6:].split("=", 1) for line in lines if line.startswith("build\t") and "=" in line[6:])
    if settings.get("GOOS") != goos or settings.get("GOARCH") != arch or settings.get("CGO_ENABLED") != "0":
        raise ValueError("runner Go target/CGO metadata does not match the native package")
    source.seek(0)


def _profiles(path, arch):
    with _regular(path, 2 << 20) as source:
        try:
            data = json.load(source)
        except (ValueError, RecursionError):
            raise ValueError("invalid prepared profiles JSON") from None
    if not isinstance(data, dict) or data.get("schema_version") != "1" or not isinstance(data.get("profiles"), list):
        raise ValueError("prepared profiles schema_version must be 1")
    selected = []
    for kind in ("cpu", "cuda"):
        candidates = [p for p in data["profiles"] if isinstance(p, dict) and p.get("platform") == "linux/" + arch and p.get("accelerator") == kind]
        if len(candidates) > 1 or (kind == "cpu" and len(candidates) != 1):
            raise ValueError("exactly one native CPU profile and at most one CUDA profile are required")
        if not candidates:
            continue
        entry = candidates[0]
        if (not isinstance(entry.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entry["id"])
                or entry.get("guard_version") != "1" or not isinstance(entry.get("image_digest"), str) or not IMAGE.fullmatch(entry["image_digest"])):
            raise ValueError("profiles require prepared immutable image digests and guard_version 1")
        selected.append({name: entry[name] for name in PROFILE_FIELDS})
    if len({p["id"] for p in selected}) != len(selected):
        raise ValueError("selected profile ids must be unique")
    return selected


def _inspect(docker, reference, profile):
    try:
        result = subprocess.run([*docker, "image", "inspect", reference], check=True, capture_output=True, timeout=30)
        if len(result.stdout) > 2 << 20:
            raise ValueError("Docker inspection exceeds bound")
        records = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("cannot inspect the explicitly cached image") from None
    except (ValueError, RecursionError):
        raise ValueError("invalid Docker image inspection") from None
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("Docker must inspect one immutable image")
    image = records[0]
    labels = image.get("Config", {}).get("Labels", {}) if isinstance(image.get("Config"), dict) else {}
    if (not isinstance(image.get("Id"), str) or not LOCAL_ID.fullmatch(image["Id"]) or image.get("Os") != "linux"
            or "linux/" + str(image.get("Architecture")) != profile["platform"] or not isinstance(labels, dict)
            or labels.get("io.campus.guard") != "1" or labels.get("io.campus.accelerator") != profile["accelerator"]
            or (LOCAL_ID.fullmatch(profile["image_digest"]) and image["Id"] != profile["image_digest"])):
        raise ValueError("cached image identity/platform/guard does not match the approved profile")
    return image


class _BoundedWriter:
    def __init__(self, destination, limit):
        self.destination, self.limit, self.total = destination, limit, 0

    def write(self, data):
        self.total += len(data)
        if self.total > self.limit:
            raise ValueError("compressed image exceeds package bound")
        return self.destination.write(data)

    def flush(self):
        self.destination.flush()


def gzip_stream(source, destination, *, limit=MAX_IMAGE):
    with gzip.GzipFile(fileobj=_BoundedWriter(destination, limit), mode="wb", filename="", compresslevel=1, mtime=0) as compressed:
        shutil.copyfileobj(source, compressed, length=CHUNK)


class _Tee:
    def __init__(self, source, destination):
        self.source, self.destination, self.total = source, destination, 0

    def read(self, size):
        if size < 0 or size > CHUNK:
            raise ValueError("unbounded image export read")
        value = self.source.read(size)
        self.total += len(value)
        if self.total > MAX_RAW:
            raise ValueError("uncompressed image export exceeds bound")
        self.destination.write(value)
        return value


def _archive(source, destination, image_id):
    metadata, blobs, legacy_configs = {}, set(), set()
    with gzip.GzipFile(fileobj=_BoundedWriter(destination, MAX_IMAGE), mode="wb", filename="", compresslevel=1, mtime=0) as compressed:
        tee = _Tee(source, compressed)
        try:
            with tarfile.open(fileobj=tee, mode="r|") as archive:
                for number, entry in enumerate(archive):
                    parts = PurePosixPath(entry.name).parts
                    if number >= 100000 or not parts or entry.name.startswith("/") or ".." in parts or "\\" in entry.name or not (entry.isfile() or entry.isdir()):
                        raise ValueError("unsafe Docker export archive")
                    if not entry.isfile():
                        continue
                    if entry.size > MAX_RAW:
                        raise ValueError("Docker archive member exceeds bound")
                    value = archive.extractfile(entry)
                    hashed = hashlib.sha256()
                    small = bytearray() if entry.name in {"index.json", "manifest.json"} else None
                    while chunk := value.read(CHUNK):
                        hashed.update(chunk)
                        if small is not None:
                            if len(small) + len(chunk) > 2 << 20:
                                raise ValueError("Docker archive metadata exceeds bound")
                            small.extend(chunk)
                    actual = hashed.hexdigest()
                    if re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", entry.name):
                        if actual != entry.name.rsplit("/", 1)[-1]:
                            raise ValueError("Docker archive blob digest mismatch")
                        blobs.add("sha256:" + actual)
                    elif re.fullmatch(r"[a-f0-9]{64}\.json", entry.name):
                        if actual != entry.name[:-5]:
                            raise ValueError("Docker archive config digest mismatch")
                        legacy_configs.add(entry.name)
                    if small is not None:
                        metadata[entry.name] = json.loads(small)
            while tee.read(CHUNK):
                pass
        except (tarfile.TarError, EOFError, RecursionError):
            raise ValueError("invalid Docker image archive") from None
    index = metadata.get("index.json")
    if isinstance(index, dict):
        roots = index.get("manifests")
        if not isinstance(roots, list) or not any(isinstance(ref, dict) and ref.get("digest") == image_id for ref in roots) or image_id not in blobs:
            raise ValueError("exported OCI index does not match the inspected image ID")
        return
    manifest = metadata.get("manifest.json")
    if not isinstance(manifest, list) or not any(isinstance(item, dict) and item.get("Config") == image_id[7:] + ".json" and item.get("Config") in legacy_configs for item in manifest):
        raise ValueError("exported Docker config does not match the inspected image ID")


def _export(docker, reference, destination, image_id):
    process = None
    try:
        process = subprocess.Popen([*docker, "image", "save", reference], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        timer = threading.Timer(900, process.kill)
        timer.daemon = True
        timer.start()
        try:
            with destination.open("xb") as output:
                _archive(process.stdout, output, image_id)
                output.flush()
                os.fsync(output.fileno())
            if process.wait(timeout=30) != 0:
                raise RuntimeError("Docker image export failed; no package published")
        finally:
            timer.cancel()
    except OSError:
        raise RuntimeError("cannot export the cached Docker image") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def _private(path):
    path.chmod(0o700)
    if os.name == "nt":
        try:
            output = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True, timeout=15)
            sid = next(csv.reader(output.stdout.splitlines()))[-1]
            if not re.fullmatch(r"S-1-(?:\d+-)*\d+", sid):
                raise ValueError("cannot determine the package owner SID")
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "*" + sid + ":(OI)(CI)F"], capture_output=True, check=True, timeout=15)
        except (subprocess.SubprocessError, OSError, StopIteration):
            raise RuntimeError("cannot protect the private package ACL") from None


def _publish(staging, destination):
    if os.name == "nt":
        os.rename(staging, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        if operation is None:
            raise RuntimeError("atomic no-replace publication unavailable")
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = operation(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        operation = library.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = operation(os.fsencode(staging), os.fsencode(destination), 4)
    else:
        raise RuntimeError("atomic no-replace publication unavailable")
    if result:
        number = ctypes.get_errno()
        if number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(number, os.strerror(number), destination)
        raise OSError(number, os.strerror(number), destination)


def write_launchers(root, goos, runner_sha, manifest_sha):
    if not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in (runner_sha, manifest_sha)):
        raise ValueError("launcher pins must be SHA-256 digests")
    names = ["Connect.command"] if goos == "darwin" else ["Connect.cmd", "Connect.ps1"]
    for name in names:
        template = Path(__file__).parent / "connect" / (name + ".in")
        content = template.read_text(encoding="utf-8").replace("@RUNNER_SHA256@", runner_sha).replace("@MANIFEST_SHA256@", manifest_sha)
        if name.endswith(".cmd"):
            content = content.replace("\n", "\r\n")
        _write(root / name, content, 0o755 if name.endswith(".command") else 0o600)


def prepare_kit(profiles, binary, out, *, target, ca_file=None, invite_file=None, endpoint=None, docker=("docker",)):
    if target not in {"darwin-arm64", "windows-amd64"}:
        raise ValueError("target must be darwin-arm64 or windows-amd64")
    profiles, binary, out = (Path(os.path.abspath(os.fspath(value))) for value in (profiles, binary, out))
    _no_links(out)
    if os.path.lexists(out):
        raise FileExistsError(out)
    goos, arch = target.split("-")
    selected = _profiles(profiles, arch)
    if goos == "darwin" and any(p["accelerator"] == "cuda" for p in selected):
        raise ValueError("the Mac package supports native CPU containers only")
    if endpoint is not None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or "\\" in endpoint or any(ord(c) < 33 or ord(c) > 126 for c in endpoint):
            raise ValueError("endpoint must be a credential-free HTTPS URL")
        try:
            parsed.port
        except ValueError:
            raise ValueError("invalid endpoint port") from None
    optional = {}
    for key, value, name in (("ca_file", ca_file, "ca.pem"), ("invite_file", invite_file, "invite.txt")):
        if value is not None:
            with _regular(Path(os.path.abspath(os.fspath(value))), 2 << 20) as stream:
                payload = stream.read((2 << 20) + 1)
                if not payload or b"\0" in payload:
                    raise ValueError("empty or invalid private connection metadata")
                if key == "invite_file":
                    try:
                        lines = payload.decode("utf-8").splitlines()
                    except UnicodeError:
                        raise ValueError("invitation must be one UTF-8 URL or token line") from None
                    if len(lines) != 1 or not lines[0] or len(lines[0]) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in lines[0]):
                        raise ValueError("invitation must be one UTF-8 URL or token line")
                optional[key] = (name, payload)
    with _regular(binary, 128 << 20) as source:
        _binary(source, target)
        runner_sha = _source_sha(source)
        cached = [(profile, _inspect(docker, profile["image_digest"], profile)) for profile in selected]
        out.parent.mkdir(parents=True, exist_ok=True)
        _no_links(out)
        parent = out.parent.resolve(strict=True)
        staging = Path(tempfile.mkdtemp(prefix=".campus-connect-", dir=parent))
        try:
            _private(staging)
            runner = staging / ("campus.exe" if goos == "windows" else "campus")
            with runner.open("xb") as output:
                shutil.copyfileobj(source, output, length=CHUNK)
                output.flush()
                os.fsync(output.fileno())
            runner.chmod(0o755)
            if _sha(runner) != runner_sha:
                raise ValueError("runner changed during package preparation")
            (staging / "images").mkdir(mode=0o700)
            images = []
            for profile, inspection in cached:
                tags = inspection.get("RepoTags", [])
                if not isinstance(tags, list):
                    tags = []
                names = sorted(tag for tag in tags if isinstance(tag, str) and TAG.fullmatch(tag))
                reference = names[0] if names else inspection["Id"]
                if _inspect(docker, reference, profile)["Id"] != inspection["Id"]:
                    raise ValueError("cached image reference changed before export")
                archive = staging / "images" / (profile["accelerator"] + ".tar.gz")
                _export(docker, reference, archive, inspection["Id"])
                if _inspect(docker, reference, profile)["Id"] != inspection["Id"]:
                    raise ValueError("cached image reference changed during export")
                images.append({**_file_ref(staging, archive), **{key: profile[key] for key in ("image_digest", "accelerator", "platform")}})
            _write(staging / "profiles.json", json.dumps({"schema_version": "1", "profiles": selected}, indent=2) + "\n")
            manifest = {"schema_version": "1", "os": goos, "arch": arch, "runner": _file_ref(staging, runner), "profiles": _file_ref(staging, staging / "profiles.json"), "images": images}
            for key, (name, payload) in optional.items():
                _write(staging / name, payload)
                manifest[key] = _file_ref(staging, staging / name)
            if endpoint is not None:
                manifest["endpoint"] = endpoint
            _write(staging / "connection.json", json.dumps(manifest, indent=2) + "\n")
            manifest_sha = _sha(staging / "connection.json")
            write_launchers(staging, goos, manifest["runner"]["sha256"], manifest_sha)
            _write(staging / "README.txt", "Campus connection package\n\nOpen Connect.command on Mac, or Connect.cmd on Windows.\nDocker Desktop and AC power are required; host Python/Go are unnecessary.\nKeep this whole folder together. Connection stays visible; no autostart is installed.\nUse --check-only for setup, --drain to finish active work, or --stop to stop it.\nMac: sh ./Connect.command --check-only (or --drain / --stop).\nWindows advanced entry point: .\\Connect.ps1 --check-only (or --drain / --stop).\nGPU detection never opts you in; CUDA needs explicit consent and a real probe.\nThe image archive is offline and immutable; canonical participation metadata is\nverified after loading, rather than assumed from an exported tag.\nIf macOS blocks a file, use the normal per-file Privacy & Security decision\nafter verifying this trusted package. Do not disable system security.\n" + ("\nPRIVATE DEVICE PACKAGE: contains an invitation. Do not publish or forward.\n" if invite_file else "\nNo invitation is bundled. Paste your private invitation when prompted.\n") + "\nManifest SHA-256: " + manifest_sha + "\n")
            total = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            if total > MAX_PACKAGE or (staging / "connection.json").stat().st_size > 65536:
                raise ValueError("connection package exceeds size bounds")
            _no_links(out)
            _publish(staging, out)
            return out
        finally:
            if os.path.lexists(staging):
                if not _linked(staging.lstat()) and staging.resolve(strict=True).parent == parent and staging.name.startswith(".campus-connect-"):
                    shutil.rmtree(staging)
                else:
                    raise RuntimeError("refusing package cleanup outside private staging")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--target", required=True, choices=("darwin-arm64", "windows-amd64"))
    parser.add_argument("--out", required=True, help="new, nonexistent output directory")
    parser.add_argument("--ca-file")
    parser.add_argument("--invite-file", help="explicit PRIVATE per-device invitation; never publish this package")
    parser.add_argument("--endpoint")
    args = parser.parse_args(argv)
    try:
        out = prepare_kit(args.profiles, args.binary, args.out, target=args.target, ca_file=args.ca_file, invite_file=args.invite_file, endpoint=args.endpoint)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, "Connection package preparation failed: " + str(error) + "\n")
    print(json.dumps({"directory": str(out), "target": args.target, "manifest_sha256": _sha(out / "connection.json"), "private_invitation": args.invite_file is not None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
