"""Validate the complete, immutable archive before a checkpoint can be published."""

import hashlib
import json
from zipfile import ZipFile

from pydantic import ValidationError

from .. import dto
from ..errors import Problem


def validate_checkpoint(path, compatibility_digest, required_files):
    try:
        with ZipFile(path) as archive:
            entry = archive.getinfo("campus-checkpoint.json")
            if entry.file_size > 65_536:
                raise ValueError("Manifest exceeds the limit")
            manifest = dto.CheckpointManifest.model_validate_json(archive.read(entry))
            if manifest.compatibility_digest != compatibility_digest:
                raise ValueError("Incompatible checkpoint")
            expected = {file.path: file for file in manifest.files}
            actual = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
            if (
                len(expected) != len(manifest.files)
                or not set(required_files) <= expected.keys()
                or actual != set(expected) | {"campus-checkpoint.json"}
            ):
                raise ValueError("Incomplete checkpoint")
            for name, file in expected.items():
                if archive.getinfo(name).file_size != file.size_bytes:
                    raise ValueError("Incorrect file size")
                with archive.open(name) as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != file.sha256:
                        raise ValueError("Incorrect file checksum")
    except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise Problem(
            "INVALID_CHECKPOINT", "Checkpoint manifest or required files are incomplete", 422
        ) from exc
