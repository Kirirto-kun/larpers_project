"""Explicit online wheel preparation. Never invoked by a worker/demo task."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from environments.offline import (MAX_HOUSE_BYTES, MAX_WHEELS, PIP_VERSION, SUPPORTED_PLATFORMS,
                                  _distributions, _base_sites, _fail, _read_regular,
                                  inspect_wheel, parse_requirements)


def build_manifest(directory, *, platform, requirements):
    if platform not in SUPPORTED_PLATFORMS:
        _fail("unsupported wheelhouse target platform")
    paths = sorted(Path(directory).iterdir())
    if len(paths) > MAX_WHEELS or any(path.suffix != ".whl" for path in paths):
        _fail("wheelhouse must contain bounded binary wheels only")
    files = [inspect_wheel(path)[0] for path in paths]
    if sum(file["size_bytes"] for file in files) > MAX_HOUSE_BYTES:
        _fail("wheelhouse total bytes exceed quota")
    return {"schema_version": "1", "kind": "campus-wheelhouse", "platform": platform,
            "python_version": "3.11", "requirements": list(requirements), "files": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--platform", choices=sorted(SUPPORTED_PLATFORMS), required=True)
    parser.add_argument("--python-version", choices=["311"], default="311")
    args = parser.parse_args(argv)
    staging = None
    try:
        requirements = parse_requirements(_read_regular(args.requirements), exact=True)
        if not requirements:
            _fail("wheelhouse online preparation requires exact pinned requirements")
        installed = _distributions(_base_sites())
        if installed.get("pip") is None or installed["pip"].version != PIP_VERSION:
            _fail(f"online preparer requires pinned pip=={PIP_VERSION}")
        destination = Path(args.to).absolute()
        if destination.exists() or destination.is_symlink():
            _fail("wheelhouse destination already exists; choose a new immutable directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".campus-wheelhouse-", dir=destination.parent))
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
        env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
        command = [sys.executable, "-I", "-m", "pip", "download", "--only-binary=:all:", "--no-cache-dir", "--no-input",
                   "--disable-pip-version-check", "--index-url", "https://pypi.org/simple", "--platform", args.platform,
                   "--python-version", args.python_version, "--implementation", "cp", "--abi", "cp311",
                   "--dest", str(staging), *[str(requirement) for requirement in requirements]]
        # Explicit online command only; finite runtime and no inherited pip config.
        subprocess.run(command, env=env, check=True, timeout=300)
        manifest = build_manifest(staging, platform=args.platform, requirements=[str(value) for value in requirements])
        raw = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with (staging / "manifest.json").open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        for path in staging.iterdir():
            path.chmod(0o444)
        staging.rename(destination)
        staging = None
        print(json.dumps({"wheelhouse": str(destination), "files": len(manifest["files"]), "platform": args.platform}))
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Wheelhouse preparation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
