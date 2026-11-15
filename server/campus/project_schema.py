"""Bounded, executable project contracts, independent of coordinator state.

Paths are portable project-relative names. Globs use case-sensitive POSIX path
segments: ``*``, ``?`` and character classes stay within a segment; ``**`` is
an entire segment matching zero or more directories.
"""

import fnmatch
import unicodedata
from functools import cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .artifacts.storage import safe_relative, secret_path
from .errors import Problem

MAX_INPUTS = 512
MAX_STAGES = 24
MAX_GLOBS = 32
MAX_PATH = 512
MAX_ARGV_BYTES = 65_536
MAX_DISK_BYTES = 512 << 20

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")]
PathText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_PATH)]


def path_key(path: str) -> str:
    """Compare portable paths consistently across Linux, Windows and macOS."""
    return unicodedata.normalize("NFC", path).casefold()


def validate_relative(path: str, *, glob: bool = False, data: bool = False) -> str:
    try:
        parsed = safe_relative(path)
    except Problem as exc:
        raise ValueError(exc.message) from exc
    if (
        len(path.encode("utf-8")) > MAX_PATH
        or len(parsed.parts) > 32
        or any(ord(char) == 127 or char in '<>"|' for char in path)
        or not glob
        and any(char in "*?[]" for char in path)
        or secret_path(parsed)
        or any(part.casefold() in {".campus", ".codex"} for part in parsed.parts)
    ):
        raise ValueError("Project paths must be bounded portable names outside secret directories")
    if data and (path_key(parsed.parts[0]) in {"stages", "project.zip"}):
        raise ValueError("Data path uses a reserved sandbox name")
    if glob and (
        sum(part == "**" for part in parsed.parts) > 4
        or any("**" in part and part != "**" for part in parsed.parts)
        or sum(path.count(char) for char in "*?[") > 64
    ):
        raise ValueError("Project globs must use bounded path segments")
    return path


def distinct_paths(paths: list[str]) -> bool:
    keys = [path_key(path) for path in paths]
    return len(set(keys)) == len(keys) and not any(
        left.startswith(right + "/") for left in keys for right in keys if left != right
    )


def matches_glob(path: str, pattern: str) -> bool:
    """Match a validated path without filesystem access or exponential globbing."""
    parts, patterns = path.split("/"), pattern.split("/")

    @cache
    def match(part: int, glob: int) -> bool:
        if glob == len(patterns):
            return part == len(parts)
        if patterns[glob] == "**":
            return match(part, glob + 1) or part < len(parts) and match(part + 1, glob)
        return (
            part < len(parts)
            and fnmatch.fnmatchcase(parts[part], patterns[glob])
            and match(part + 1, glob + 1)
        )

    return match(0, 0)


class ProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def portable_json(cls, value):
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if "\x00" in item:
                    raise ValueError("NUL characters are not supported")
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return value


class ProjectResources(ProjectModel):
    cpu_millis: int = Field(default=1000, strict=True, ge=1, le=4000)
    memory_bytes: int = Field(default=512 << 20, strict=True, ge=32 << 20, le=4 << 30)
    disk_bytes: int = Field(default=256 << 20, strict=True, ge=1 << 20, le=MAX_DISK_BYTES)
    # Only CUDA variants reserve this amount; CPU variants always reserve zero.
    vram_bytes: int = Field(default=1 << 30, strict=True, ge=0, le=4 << 30)


class ProjectOutput(ProjectModel):
    path: PathText
    max_bytes: int = Field(strict=True, ge=1, le=MAX_DISK_BYTES)
    required: bool = Field(default=True, strict=True)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        value = validate_relative(value)
        if value.split("/", 1)[0].casefold() == "stages":
            raise ValueError("Output path uses the reserved intermediate download namespace")
        return value


class ProjectStage(ProjectModel):
    id: Identifier
    kind: Literal["map", "reduce"]
    inputs: list[PathText] = Field(default_factory=list, max_length=MAX_GLOBS)
    depends_on: list[Identifier] = Field(default_factory=list, max_length=MAX_STAGES)
    command: list[Annotated[str, StringConstraints(strict=True, max_length=4096)]] = Field(
        min_length=1, max_length=64
    )
    outputs: list[ProjectOutput] = Field(min_length=1, max_length=32)
    accelerator: Literal["auto", "cpu", "cuda"] = "auto"
    resources: ProjectResources = Field(default_factory=ProjectResources)
    timeout_seconds: int = Field(default=300, strict=True, ge=1, le=86_400)

    @field_validator("inputs")
    @classmethod
    def valid_inputs(cls, value):
        for pattern in value:
            validate_relative(pattern, glob=True, data=True)
        if len({path_key(pattern) for pattern in value}) != len(value):
            raise ValueError("Input globs must be unique without case aliases")
        return value

    @model_validator(mode="after")
    def executable_contract(self):
        if self.kind == "map" and not self.inputs:
            raise ValueError("Map stages require explicit data input globs")
        if len({path_key(name) for name in self.depends_on}) != len(self.depends_on):
            raise ValueError("Dependencies must be unique")
        if not self.command[0] or any(char in self.command[0] for char in "{}"):
            raise ValueError("The executable must be a non-empty literal argument")
        if sum(len(arg.encode("utf-8")) for arg in self.command) > MAX_ARGV_BYTES:
            raise ValueError("Project command exceeds the native argument limit")
        for argument in self.command:
            remaining = argument.replace("{accelerator}", "")
            if self.kind == "map":
                remaining = remaining.replace("{input}", "")
            if "{" in remaining or "}" in remaining:
                raise ValueError("Only map {input} and {accelerator} command templates are supported")
        if not distinct_paths([output.path for output in self.outputs]):
            raise ValueError("Output paths must be distinct without case or directory aliases")
        disk = self.resources.disk_bytes
        temporary = max(4096, min(64 << 20, disk // 8))
        if sum(output.max_bytes for output in self.outputs) + 2 * temporary >= disk:
            raise ValueError("Declared outputs and sandbox quotas exceed reserved disk")
        if self.accelerator == "cuda" and not self.resources.vram_bytes:
            raise ValueError("CUDA stages require an explicit positive VRAM reservation")
        return self


class ProjectConfig(ProjectModel):
    schema_version: Literal["1"]
    name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
    ]
    data: list[PathText] = Field(max_length=MAX_GLOBS)
    prompt: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=8000)] | None = None
    stages: list[ProjectStage] = Field(min_length=1, max_length=MAX_STAGES)

    @field_validator("data")
    @classmethod
    def valid_data(cls, value):
        for pattern in value:
            validate_relative(pattern, glob=True, data=True)
        if len({path_key(pattern) for pattern in value}) != len(value):
            raise ValueError("Data globs must be unique without case aliases")
        return value

    @model_validator(mode="after")
    def ordered_dag(self):
        known = set()
        folded = set()
        for stage in self.stages:
            if path_key(stage.id) in folded:
                raise ValueError("Stage IDs must be unique without case aliases")
            if not set(stage.depends_on) <= known:
                raise ValueError("Stage dependencies must name preceding stages")
            known.add(stage.id)
            folded.add(path_key(stage.id))
        return self
