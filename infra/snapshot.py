"""Create and restore coordinator snapshots. Run from the repository root."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ["docker", "compose", "-f", str(ROOT / "infra/compose.yaml")]
if (ROOT / ".env").is_file():
    BASE.extend(["--env-file", str(ROOT / ".env")])
FILES = ("database.dump", "artifacts.tar")


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, check=True, **kwargs)


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def backup(destination, project):
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit("Backup destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [*BASE, "-p", project]
    running = run(
        [*command, "ps", "--status", "running", "--services"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if "db" not in running:
        raise SystemExit("The source database must be running.")
    with tempfile.TemporaryDirectory(
        prefix=".campus-backup-", dir=destination.parent
    ) as name:
        stage = Path(name) / "snapshot"
        stage.mkdir(mode=0o700)
        if "api" in running:
            run([*command, "stop", "api"])
        try:
            with (stage / "database.dump").open("xb") as target:
                run(
                    [
                        *command,
                        "exec",
                        "-T",
                        "db",
                        "pg_dump",
                        "-U",
                        "campus",
                        "-Fc",
                        "campus",
                    ],
                    stdout=target,
                )
            with (stage / "artifacts.tar").open("xb") as target:
                run(
                    [
                        *command,
                        "run",
                        "--rm",
                        "-T",
                        "--no-deps",
                        "--entrypoint",
                        "tar",
                        "api",
                        "-C",
                        "/state",
                        "-cf",
                        "-",
                        ".",
                    ],
                    stdout=target,
                )
        finally:
            if "api" in running:
                run([*command, "start", "api"])
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "sha256": {file: checksum(stage / file) for file in FILES},
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(stage, destination)


def restore(source, project):
    command = [*BASE, "-p", project]
    source = source.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format") != 1 or any(
        manifest["sha256"].get(file) != checksum(source / file) for file in FILES
    ):
        raise SystemExit("Snapshot integrity check failed.")
    existing = run(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        capture_output=True,
        text=True,
    ).stdout
    containers = run([*command, "ps", "-aq"], capture_output=True, text=True).stdout
    if existing.strip() or containers.strip():
        raise SystemExit(
            "Restore requires a fresh Compose project with unused volumes."
        )
    print(f"Restoring snapshot dated {manifest['created_at']}", flush=True)
    run([*command, "up", "-d", "--wait", "db"])
    with (source / "database.dump").open("rb") as data:
        run(
            [
                *command,
                "exec",
                "-T",
                "db",
                "pg_restore",
                "--exit-on-error",
                "-U",
                "campus",
                "-d",
                "campus",
            ],
            stdin=data,
        )
    with (source / "artifacts.tar").open("rb") as data:
        run(
            [
                *command,
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "tar",
                "api",
                "-C",
                "/state",
                "-xf",
                "-",
            ],
            stdin=data,
        )
    run(
        [
            *command,
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "python",
            "api",
            "-m",
            "campus.admin",
            "reconcile",
        ]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        parser.error("invalid Compose project name")
    if args.action == "backup":
        backup(args.directory, args.project)
    else:
        restore(args.directory, args.project)


if __name__ == "__main__":
    main()
