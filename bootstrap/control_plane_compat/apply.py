"""Generate/apply the narrow diagnostic overlay to pristine control plane 96bfe6d."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path

HASHES = {
    "main.py": "71850480653a4c8513fb6e01be113914934200ed4a2f7cd0cc511ace16289c04",
    "jobs/scheduler.py": "62cb6a44b20af5a8e083064be6b28220df273b3e971c21a3da00fcff56ed6bba",
}


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("pristine patch anchor is not unique")
    return value.replace(old, new, 1)


def prepare(campus):
    originals = {}
    for path, expected in HASHES.items():
        raw = (campus / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"{path} is not pristine control-plane 96bfe6d")
        originals[path] = raw.decode("utf-8").replace("\r\n", "\n")
    main = originals["main.py"]
    main = replace_once(main, "from . import auth, dto, events, observability, search", "from . import auth, dto, events, observability, search, worker_compat")
    main = replace_once(main,
        "return scheduler.claim_work(db, runtime, actor, session_id)",
        "policy = worker_compat.policy_for(actor, request.headers.get(\"user-agent\"))\n                return scheduler.claim_work(db, runtime, actor, session_id, worker_policy=policy)")
    main = replace_once(main, "if assignment:\n                return assignment", "if assignment:\n                return JSONResponse(assignment) if isinstance(assignment, dict) else assignment")
    main = replace_once(main,
        "def complete_attempt(attempt_id: UUID, body: dto.Complete, ctx: ContextDep) -> dto.CompletionReceipt:\n        return leases.complete_attempt(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)",
        "def complete_attempt(request: Request, attempt_id: UUID, body: dto.Complete, ctx: ContextDep) -> dto.CompletionReceipt:\n"
        "        policy = worker_compat.policy_for(ctx.actor, request.headers.get(\"user-agent\"))\n"
        "        if policy:\n"
        "            try:\n"
        "                manifest = worker_compat.result_manifest(payload(body.manifest), policy)\n"
        "            except ValueError as exc:\n"
        "                raise Problem(\"INVALID_RESULT\", str(exc), 422) from exc\n"
        "            body = body.model_copy(update={\"manifest\": dto.ResultManifest.model_validate(manifest)})\n"
        "        return leases.complete_attempt(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)")
    scheduler = originals["jobs/scheduler.py"]
    scheduler = replace_once(scheduler, "from .. import dto", "from .. import dto, worker_compat")
    scheduler = replace_once(scheduler, "def claim_work(db, app, actor, session_id):", "def claim_work(db, app, actor, session_id, *, worker_policy=None):")
    scheduler = replace_once(scheduler,
        "variant = next((v for v in variants if compatible(node, v, remaining)), None)",
        "variant = next((v for v in variants if compatible(node, v, remaining) and worker_compat.supports(stage, v, worker_policy)), None)")
    scheduler = replace_once(scheduler,
        "return lease(db, app, node, session, current, job, task, owner, stage, variant)",
        "return lease(db, app, node, session, current, job, task, owner, stage, variant, worker_policy=worker_policy)")
    scheduler = replace_once(scheduler,
        "def lease(db, app, node, session, current, job, task, owner, stage, variant):",
        "def lease(db, app, node, session, current, job, task, owner, stage, variant, *, worker_policy=None):")
    scheduler = replace_once(scheduler, "return dto.Assignment(", "value = dto.Assignment(")
    scheduler = replace_once(scheduler,
        "        parameters={**stage.parameters, **task.parameters},\n    )",
        "        parameters={**stage.parameters, **task.parameters},\n    )\n"
        "    return worker_compat.assignment(\n"
        "        payload(value), worker_policy,\n"
        "        remaining_ms=int((deadline - current).total_seconds() * 1000),\n"
        "    ) if worker_policy else value")
    changes = {"main.py": main, "jobs/scheduler.py": scheduler,
               "worker_compat.py": (Path(__file__).parent / "worker_compat.py").read_text(encoding="utf-8")}
    for name, code in changes.items():
        compile(code, name, "exec")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campus", type=Path, help="pristine server/campus directory")
    parser.add_argument("--apply", action="store_true", help="write the reviewed overlay")
    args = parser.parse_args()
    changes = prepare(args.campus.resolve(strict=True))
    if args.apply:
        for name, code in changes.items():
            (args.campus / name).write_text(code, encoding="utf-8", newline="\n")
    print("Applied" if args.apply else "Validated", "diagnostic overlay:", ", ".join(changes))


if __name__ == "__main__":
    main()
