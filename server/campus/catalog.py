import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import dto
from .errors import Problem


class Adapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: dto.AdapterRef
    image_ids: list[dto.Identifier]
    policies: list[dto.Identifier]
    interactive: bool = True
    checkpoints: bool = False
    checkpoint_files: list[dto.FilePath] = Field(default_factory=list)
    parallel: bool = False
    adaptive_batches: bool = False
    initial_batch_units: int = Field(default=8, ge=1, le=10_000)
    max_batch_units: int = Field(default=256, ge=1, le=10_000)
    unit: str = "task"
    weight: int = Field(default=1, ge=1, le=10_000)
    command: list[str] = Field(default_factory=list)
    builtin: Literal["project"] | None = None
    recipe: dto.Recipe | None = None
    search_stage: dto.Stage | None = None
    probe_stage: dto.Stage | None = None


class CatalogData(BaseModel):
    environments: list[dto.Environment] = Field(default_factory=list)
    adapters: list[Adapter] = Field(default_factory=list)


class Catalog:
    def __init__(self, filename=None):
        data = CatalogData.model_validate_json(Path(filename).read_text()) if filename else CatalogData()
        self.environments = {item.id: item for item in data.environments}
        self.adapters = {(item.ref.id, item.ref.version): item for item in data.adapters}
        self.recipes = {item.recipe.id: item.recipe for item in data.adapters if item.recipe}
        if (
            len(self.environments) != len(data.environments)
            or len(self.adapters) != len(data.adapters)
            or len(self.recipes) != sum(item.recipe is not None for item in data.adapters)
        ):
            raise ValueError("Catalog IDs must be unique")
        for item in self.adapters.values():
            if not set(item.image_ids) <= self.environments.keys():
                raise ValueError("Adapter references an unregistered environment")
            if item.adaptive_batches and not item.parallel:
                raise ValueError(
                    "Adaptive batches require an adapter accepting any assigned item subset"
                )
            if item.initial_batch_units > item.max_batch_units:
                raise ValueError("Initial batch exceeds maximum batch size")
            if item.checkpoints and not item.checkpoint_files:
                raise ValueError("Checkpoint adapters must declare every required state file")
            if item.builtin and (
                item.ref.id not in {"campus.project-map", "campus.project-reduce"}
                or item.ref.version != "1"
                or item.command
            ):
                raise ValueError(
                    "Only the registered project metadata adapters may use the builtin runtime"
                )
            if item.parallel and not item.command and not item.builtin:
                raise ValueError("A parallel adapter requires a bounded runtime command")

    def adapter(self, ref):
        adapter = self.adapters.get((ref.id, ref.version))
        if adapter is None:
            raise Problem("UNSUPPORTED_ADAPTER", "The adapter version is not registered", 422)
        return adapter

    def planner_context(self):
        adapters = [a for a in self.adapters.values() if a.interactive]
        image_ids = {image for adapter in adapters for image in adapter.image_ids}
        return json.loads(
            CatalogData(
                environments=[e for e in self.environments.values() if e.id in image_ids],
                adapters=[a.model_copy(update={"command": []}) for a in adapters],
            ).model_dump_json()
        )
