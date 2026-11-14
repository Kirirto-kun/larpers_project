"""Build a local, offline macOS ARM64 readiness kit from already-cached inputs.

The maker needs Python and Docker. The receiving Mac needs Docker Desktop and
macOS built-ins only. This command never builds/pulls images or contacts a pool.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import errno
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CHUNK = 65536
_PROFILE_FIELDS = ("id", "accelerator", "platform", "image_digest", "guard_version")
_PAYLOADS = ("README.md", "campus", "check.sh", "image.tar.gz", "profiles.json")
_CPU_CHECK = '''import json
import torch
torch.set_num_threads(1)
torch.set_num_interop_threads(1)
value = torch.tensor([[1.0, 2.0], [3.0, 4.0]], dtype=torch.float32, device="cpu")
result = value @ value
expected = torch.tensor([[7.0, 10.0], [15.0, 22.0]], dtype=torch.float32, device="cpu")
assert result.device.type == "cpu" and torch.equal(result, expected), "tiny CPU calculation failed"
print(json.dumps({"check": "cpu_tiny_calculation", "device": "cpu", "torch_version": torch.__version__, "result": result.tolist()}))
'''


def bounded_probe_code(child_code: str = _CPU_CHECK, *, seconds: float = 45) -> str:
    """In-container Python PID1 enforces its child's deadline independently."""
    if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or not 0 < seconds <= 45:
        raise ValueError("probe deadline must be positive and at most 45 seconds")
    return "import subprocess, sys\nsubprocess.run([sys.executable, '-c', " + repr(child_code) + "], check=True, timeout=" + repr(seconds) + ")\n"


def _is_link(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _no_links(path: Path):
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if _is_link(metadata):
            raise ValueError("kit paths must not contain symlinks or reparse points")


@contextmanager
def _regular_file(path: Path, limit: int):
    _no_links(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ValueError("kit input must be a bounded regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _profile(path: Path) -> dict:
    with _regular_file(path, 1 << 20) as stream:
        try:
            source = json.loads(stream.read((1 << 20) + 1))
        except (ValueError, RecursionError):
            raise ValueError("malformed prepared profiles JSON") from None
    if not isinstance(source, dict) or source.get("schema_version") != "1" or not isinstance(source.get("profiles"), list):
        raise ValueError("prepared profiles schema_version must be 1")
    candidates = [entry for entry in source["profiles"] if isinstance(entry, dict) and entry.get("accelerator") == "cpu" and entry.get("platform") == "linux/arm64"]
    if len(candidates) != 1:
        raise ValueError("exactly one prepared CPU linux/arm64 profile is required")
    entry = candidates[0]
    if (entry.get("guard_version") != "1" or not isinstance(entry.get("id"), str) or not _ID.fullmatch(entry["id"])
            or not isinstance(entry.get("image_digest"), str) or not _IMAGE.fullmatch(entry["image_digest"])):
        raise ValueError("CPU ARM profile requires guard_version 1 and a local immutable sha256 image ID")
    # Runtime fields only: do not copy builder cache paths, model paths, custom
    # metadata, or credentials into a kit intended for another notebook.
    return {key: entry[key] for key in _PROFILE_FIELDS}


def _binary_header(stream):
    header = stream.read(32)
    if len(header) != 32:
        raise ValueError("binary is not a complete Mach-O ARM64 executable")
    magic, cpu, _, kind, _, _, _, _ = struct.unpack("<8I", header)
    if (magic, cpu, kind) != (0xFEEDFACF, 0x0100000C, 2):
        raise ValueError("binary must be a thin native Mach-O ARM64 executable")
    stream.seek(0)


def _inspect_image(docker, image_id):
    try:
        response = subprocess.run([*docker, "image", "inspect", image_id], check=True, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if len(response.stdout.encode("utf-8")) > 1 << 20:
            raise ValueError("Docker inspect response exceeds limit")
        images = json.loads(response.stdout)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("cannot inspect the cached Docker image; no kit published") from None
    except (ValueError, RecursionError):
        raise ValueError("malformed Docker image inspection") from None
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise ValueError("Docker must inspect exactly one immutable image")
    image = images[0]
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (image.get("Id") != image_id or image.get("Os") != "linux" or image.get("Architecture") != "arm64"
            or not isinstance(labels, dict) or labels.get("io.campus.guard") != "1" or labels.get("io.campus.accelerator") != "cpu"):
        raise ValueError("cached image identity/platform/CPU guard contract does not match the ARM profile")


def gzip_stream(source, destination):
    """Bounded-memory level-1 gzip; omit maker filename and timestamp metadata."""
    with gzip.GzipFile(fileobj=destination, mode="wb", filename="", compresslevel=1, mtime=0) as compressed:
        shutil.copyfileobj(source, compressed, length=_CHUNK)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, text: str):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _check_script(image_id: str) -> str:
    probe = shlex.quote(bounded_probe_code())
    return f'''#!/bin/sh
set -eu
umask 077
fail() {{ printf '%s\\n' "Campus readiness: $1" >&2; exit 1; }}
kit_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$kit_dir"
if verified=$(shasum -a 256 -c ./SHA256SUMS); then
    printf '%s\\n' "$verified"
else
    fail 'checksum verification failed; do not execute or load changed files'
fi
[ "$(uname -s)" = Darwin ] || fail 'this kit requires macOS'
[ "$(uname -m)" = arm64 ] || fail 'this kit requires native Apple Silicon arm64'
command -v docker >/dev/null 2>&1 || fail 'install/start Docker Desktop for Apple Silicon first'
records='./readiness-records'
[ ! -L "$records" ] || fail 'readiness-records must not be a symlink'
if [ -e "$records" ] && [ ! -d "$records" ]; then
    fail 'readiness-records must be a directory'
fi
mkdir -p "$records"
chmod 700 "$records"
for record in checksums.txt system.txt docker-load.log image.txt doctor.json doctor.stderr cpu-check.json cpu-check.stderr; do
    [ ! -L "$records/$record" ] || fail 'record files must not be symlinks'
    if [ -e "$records/$record" ] && [ ! -f "$records/$record" ]; then
        fail 'record destinations must be regular files'
    fi
done
printf '%s\\n' "$verified" > "$records/checksums.txt"
{{ uname -s; uname -m; }} > "$records/system.txt"
chmod u+x ./campus
if ! docker load --input ./image.tar.gz > "$records/docker-load.log" 2>&1; then
    fail 'bundled image could not be loaded; see readiness-records/docker-load.log'
fi
image_id='{image_id}'
actual=$(docker image inspect --format '{{{{.Id}}}} {{{{.Os}}}}/{{{{.Architecture}}}} {{{{index .Config.Labels "io.campus.guard"}}}} {{{{index .Config.Labels "io.campus.accelerator"}}}}' "$image_id")
[ "$actual" = "$image_id linux/arm64 1 cpu" ] || fail 'loaded image identity/platform/guard mismatch'
printf '%s\\n' "$actual" > "$records/image.txt"
if ! ./campus doctor --profiles ./profiles.json > "$records/doctor.json" 2> "$records/doctor.stderr"; then
    fail 'doctor failed; see readiness-records/doctor.json and doctor.stderr'
fi
if ! docker run --rm --label io.campus.readiness=1 \\
    --pull=never --platform=linux/arm64 --network=none --read-only \\
    --user=65532:65532 --cap-drop=ALL --security-opt=no-new-privileges:true \\
    --pids-limit=64 --memory=1073741824 --memory-swap=1073741824 --cpus=1 --restart=no \\
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=67108864 --ipc=private \\
    --env OMP_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 \\
    --env PYTHONDONTWRITEBYTECODE=1 --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \\
    --entrypoint /opt/campus/.venv/bin/python "$image_id" -c {probe} \\
    > "$records/cpu-check.json" 2> "$records/cpu-check.stderr"; then
    fail 'bounded CPU check failed; see readiness-records/cpu-check.stderr'
fi
printf '%s\\n' 'Offline Mac CPU readiness passed. Review readiness-records before returning them.'
printf '%s\\n' 'This is a readiness check; pool execution and benchmark results remain untested.'
'''


def _readme(profile: dict) -> str:
    return f'''# Offline Apple Silicon readiness kit

Target: macOS Apple Silicon (Darwin/arm64), CPU Linux containers.
Profile: `{profile["id"]}`. Immutable local image: `{profile["image_digest"]}`.

Run this only after the first notebook's local checks have finished and the
owner is ready to test the second notebook. Put the Mac on AC power and start
Docker Desktop for Apple Silicon. macOS built-ins and Docker Desktop are the
only host prerequisites; host Python/Go and model downloads are unnecessary.

1. Obtain the maker's SHA-256 of SHA256SUMS through your trusted handoff.
2. Open Terminal, change into this folder, compare that digest, and verify:

```sh
cd "/path/with spaces/到 Mac kit"
shasum -a 256 ./SHA256SUMS
shasum -a 256 -c ./SHA256SUMS
sh ./check.sh
```

Keep the supplied `campus`, `profiles.json`, `image.tar.gz`, `check.sh`, README,
and SHA256SUMS together. check.sh works when its folder contains spaces/Unicode.
The archive is deliberately cached and loaded locally; the checker never pulls
an image. No model, GPU profile, coordinator credential, or maker personal path
was added. The image contains the already-prepared runtime dependencies.

If macOS specifically blocks the verified campus file because it is marked as
quarantined, verify checksums first, then remove only that file's attribute:

```sh
xattr -d com.apple.quarantine "./campus"
sh ./check.sh
```

If macOS still refuses execution, keep the exact error for the owner instead
of changing global security settings.

The script verifies Darwin/arm64, loads and inspects the exact bundled image,
runs `./campus doctor --profiles ./profiles.json` using normal private local
state, and performs a tiny actual CPU PyTorch matrix calculation. Its container
has no network/host mounts, a read-only root, UID65532, dropped capabilities,
no-new-privileges, 64 PIDs, 1 GiB memory, 1 CPU, a 64 MiB tmpfs, and restart=no.
An in-container Python parent independently enforces a 45-second child timeout,
so losing the host Docker CLI does not remove the calculation deadline.

Review `readiness-records/` before returning it later: checksums.txt, system.txt,
image.txt, docker-load.log, doctor.json/doctor.stderr, and
cpu-check.json/cpu-check.stderr. These are local readiness records. They do not
establish a pool job, LAN/control-plane connection, distributed correctness,
GPU support, or benchmark measurement. No production control plane is bundled.

Both notebooks being on the same LAN matters for a later explicitly scheduled
coordinator test. No endpoint, invite, port, or firewall change is invented by
this kit. Do not send credentials or personal data with the records.
'''


def _publish(staging: Path, destination: Path):
    if os.name == "nt":
        os.rename(staging, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        if operation is None:
            raise RuntimeError("atomic no-replace publication unavailable")
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        operation = library.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(staging), os.fsencode(destination), 4)
    else:
        raise RuntimeError("atomic no-replace publication unavailable")
    if result:
        number = ctypes.get_errno()
        if number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(number, os.strerror(number), destination)
        raise OSError(number, os.strerror(number), destination)


def prepare_kit(profiles, binary, out, *, docker=("docker",)) -> Path:
    """Export only one already-prepared image; publish a new complete directory."""
    profiles, binary, out = (Path(os.path.abspath(os.fspath(value))) for value in (profiles, binary, out))
    _no_links(out)
    if os.path.lexists(out):
        raise FileExistsError(out)
    profile = _profile(profiles)
    with _regular_file(binary, 128 << 20) as source:
        _binary_header(source)
        _inspect_image(docker, profile["image_digest"])
        out.parent.mkdir(parents=True, exist_ok=True)
        _no_links(out)
        parent = out.parent.resolve(strict=True)
        staging = Path(tempfile.mkdtemp(prefix=".campus-kit-", dir=parent))
        try:
            with (staging / "campus").open("xb") as target:
                shutil.copyfileobj(source, target, length=_CHUNK)
                target.flush()
                os.fsync(target.fileno())
            archive = staging / "image.tar"
            try:
                subprocess.run([*docker, "image", "save", "-o", str(archive), profile["image_digest"]], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
            except (subprocess.SubprocessError, OSError):
                raise RuntimeError("Docker image export failed; no kit published") from None
            with _regular_file(archive, 1 << 40) as raw:
                if os.fstat(raw.fileno()).st_size == 0:
                    raise ValueError("Docker exported an empty image archive")
                with (staging / "image.tar.gz").open("xb") as compressed:
                    gzip_stream(raw, compressed)
                    compressed.flush()
                    os.fsync(compressed.fileno())
            archive.unlink()
            _write(staging / "profiles.json", json.dumps({"schema_version": "1", "profiles": [profile]}, indent=2) + "\n")
            _write(staging / "README.md", _readme(profile))
            _write(staging / "check.sh", _check_script(profile["image_digest"]))
            _write(staging / "SHA256SUMS", "".join(f"{_sha(staging / name)}  {name}\n" for name in _PAYLOADS))
            _no_links(out)
            _publish(staging, out)
            return out
        finally:
            if os.path.lexists(staging):
                if _is_link(staging.lstat()):
                    staging.unlink()
                elif staging.resolve(strict=True).parent == parent and staging.name.startswith(".campus-kit-"):
                    shutil.rmtree(staging)
                else:
                    raise RuntimeError("refusing cleanup outside the private kit staging directory")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, help="prepared local profiles JSON containing one CPU linux/arm64 entry")
    parser.add_argument("--binary", required=True, help="already-built thin Mach-O ARM64 campus executable")
    parser.add_argument("--out", required=True, help="new, nonexistent kit directory (target is always macos-arm64)")
    args = parser.parse_args(argv)
    try:
        out = prepare_kit(args.profiles, args.binary, args.out)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Kit preparation failed: {error}\n")
    print(json.dumps({"target": "macos-arm64", "directory": str(out), "sha256sums_sha256": _sha(out / "SHA256SUMS")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
