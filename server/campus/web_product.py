"""Small product entry points backed by the ordinary preview/job pipeline."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from . import dto
from .db import digest
from .errors import Problem
from .planner import service as planner

DATA = Path(__file__).parent / "data"
RELEASE = "runner-20261115-r7"
EXAMPLES = [
    {
        "id": "cpu",
        "name": "Расчёт на CPU",
        "description": "Обучить маленькую модель на 250 000 примеров и получить проверяемый результат.",
        "requires_gpu": False,
    },
    {
        "id": "cuda",
        "name": "Расчёт на NVIDIA GPU",
        "description": "Выполнить матричные вычисления через CUDA и скачать результат.",
        "requires_gpu": True,
    },
]


def example_plan(catalog, example_id):
    if example_id not in {"cpu", "cuda"}:
        raise Problem("NOT_FOUND", "Example was not found", 404)
    gpu = example_id == "cuda"
    adapter = catalog.adapter(dto.AdapterRef(id="campus.python", version="1"))
    environments = [catalog.environments[key] for key in adapter.image_ids]
    variants = []
    for environment in environments:
        if (environment.accelerator == "cuda") != gpu:
            continue
        variants.append(
            dto.Variant(
                variant_id=environment.variant_id,
                image_digest=environment.image_digest,
                platform=environment.platform,
                accelerator=environment.accelerator,
                resource_requirements=dto.Resources(
                    cpu_millis=1000,
                    memory_bytes=(1024 if gpu else 128) << 20,
                    disk_bytes=34 << 20,
                    gpu_slots=int(gpu),
                    vram_bytes=1 << 30 if gpu else 0,
                ),
            )
        )
    if not variants:
        raise Problem("UNSUPPORTED_ENVIRONMENT", "The example environment is unavailable", 409)
    code = (DATA / ("cuda_example.py" if gpu else "cpu_example.py")).read_text()
    return dto.JobPlan(
        plan_revision=1,
        input_manifest_digest=digest({"project_digest": None, "inputs": []}),
        execution_kind="single",
        job_deadline_at=datetime.now(UTC) + timedelta(minutes=10),
        retry_policy=dto.RetryPolicy(max_attempts=2),
        assumptions=["Небольшой проверочный расчёт в изолированном контейнере без сети."],
        stages=[
            dto.Stage(
                stage_id="example",
                adapter=adapter.ref,
                variants=variants,
                argv=["python", "-c", code],
                working_dir="/workspace",
                outputs=[dto.Output(path="model.json", max_bytes=1 << 20)],
                task_deadline_ms=60_000,
                validation_policy="structural",
                resume_policy="restart",
            )
        ],
    )


def mount_routes(app, context, prefix):
    Context = Annotated[object, Depends(context, scope="function")]

    @app.get(prefix + "/client-release", operation_id="client_release")
    def client_release(ctx: Context) -> dict:
        ctx.actor.member()
        return {
            "version": RELEASE,
            "downloads": [
                {"os": "windows", "label": "Windows", "url": f"/downloads/{RELEASE}/Connect.cmd"},
                {"os": "darwin", "label": "macOS", "url": f"/downloads/{RELEASE}/Connect.command"},
                {"os": "linux", "label": "Linux", "url": f"/downloads/{RELEASE}/Connect.command"},
            ],
            "requirements": {"docker_manual": True, "gpu_local_consent": True},
        }

    @app.get(prefix + "/examples", operation_id="list_examples")
    def examples(ctx: Context) -> list[dict]:
        ctx.actor.member()
        return EXAMPLES

    @app.post(prefix + "/examples/{example_id}/preview", status_code=202, operation_id="example_preview")
    def example_preview(example_id: str, ctx: Context) -> dto.Preview:
        return planner.plan_job(
            ctx.db,
            ctx.runtime,
            ctx.actor,
            dto.PreviewRequest(prepared_plan=example_plan(ctx.runtime.catalog, example_id)),
        )
