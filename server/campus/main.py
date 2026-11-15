import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException

from . import auth, device_settings, dto, events, observability, search, worker_compat
from . import models as m
from .artifacts import uploads
from .artifacts.storage import Storage
from .benchmarks import service as benchmarks
from .catalog import Catalog
from .config import Settings
from .db import connect, now, payload, write_lock
from .errors import Problem, missing
from .jobs import leases, scheduler, verification
from .jobs import service as jobs
from .limits import RequestRates
from .planner import provider
from .planner import service as planner
from .web_product import mount_routes

log = logging.getLogger(__name__)
Prefix = "/api/v1"
Cursor = Annotated[int, Query(ge=0, le=2**63 - 1)]


@dataclass
class Runtime:
    settings: Settings
    sessions: object
    storage: Storage
    catalog: Catalog
    planner: object


@dataclass
class Context:
    db: object
    runtime: Runtime
    actor: auth.Actor
    session_id: UUID | None


def metadata(request):
    return request.app.state.runtime


def session_header(request):
    raw = request.headers.get("x-node-session")
    try:
        return UUID(raw) if raw else None
    except ValueError as exc:
        raise Problem("INVALID_SESSION", "Invalid X-Node-Session", 422) from exc


def transaction(request: Request):
    runtime = metadata(request)
    with runtime.sessions.begin() as db:
        yield db


def context(request: Request):
    runtime = metadata(request)
    with runtime.sessions.begin() as db:
        if request.method not in {"GET", "HEAD"}:
            write_lock(db)
        actor = auth.authenticate(db, request, runtime.settings)
        yield Context(db, runtime, actor, session_header(request))


ContextDep = Annotated[Context, Depends(context, scope="function")]
DBDep = Annotated[object, Depends(transaction, scope="function")]


def error_response(request, error, fields=None):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=error.status,
        headers={"X-Request-ID": request_id},
        content=payload(
            dto.Error(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                field_errors=fields or [],
                request_id=request_id,
            )
        ),
    )


class RequestLimit:
    def __init__(self, app):
        self.app = app
        self.rates = RequestRates()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request.state.request_id = str(uuid4())
        limit = 8_388_608 if request.method == "PUT" and "/parts/" in request.url.path else 2_097_152
        read = 0

        async def bounded():
            nonlocal read
            event = await receive()
            read += len(event.get("body", b""))
            if read > limit:
                raise Problem("REQUEST_TOO_LARGE", "Request body exceeds its size limit", 413)
            return event

        async def headers(event):
            if event["type"] == "http.response.start":
                existing = {name for name, _ in event["headers"]}
                for name, value in (
                    (b"x-request-id", request.state.request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"cache-control", b"no-store"),
                ):
                    if name not in existing:
                        event["headers"].append((name, value))
            await send(event)

        path = request.url.path
        if path.startswith(f"{Prefix}/"):
            remote = request.client.host if request.client else "local"
            if path in {
                f"{Prefix}/auth/login",
                f"{Prefix}/auth/register",
                f"{Prefix}/auth/account",
                f"{Prefix}/auth/cli-login",
                f"{Prefix}/invitations/redeem",
            }:
                bucket, capacity, refill = "entry", 30, 0.5
            elif request.method != "GET":
                bucket, capacity, refill = "write", 360, 90
            else:
                bucket, capacity, refill = "read", 720, 180
            if not self.rates.take((remote, bucket), capacity, refill):
                response = error_response(
                    request, Problem("RATE_LIMITED", "Request rate exceeded", 429, retryable=True)
                )
                await response(scope, receive, headers)
                return
        size = request.headers.get("content-length")
        if size and (not size.isascii() or not size.isdecimal() or len(size) > 10 or int(size) > limit):
            response = error_response(
                request, Problem("REQUEST_TOO_LARGE", "Invalid request body size", 413)
            )
            await response(scope, receive, headers)
            return
        await self.app(scope, bounded, headers)


def maintenance(runtime, cycle):
    with runtime.sessions.begin() as db:
        scheduler.reap(db, runtime)
        benchmarks.tick(db, runtime)
        if cycle % 60 == 0:
            uploads.mark_expired(db)
            uploads.collect(db, runtime)


def verify_once(runtime):
    with runtime.sessions.begin() as db:
        candidate = verification.claim(db, runtime)
    if candidate:
        try:
            report = verification.verify_result(runtime, candidate)
        except Problem as exc:
            with runtime.sessions.begin() as db:
                verification.accept_result(db, runtime, candidate, error=exc)
        else:
            with runtime.sessions.begin() as db:
                verification.accept_result(db, runtime, candidate, report=report)


def claim_preview(runtime):
    with runtime.sessions.begin() as db:
        return planner.claim(db)


def plan_once(runtime):
    claim = claim_preview(runtime)
    if claim:
        planner.process(runtime, claim)


async def plan_loop(runtime):
    def renew(claim):
        with runtime.sessions.begin() as db:
            planner.renew_claim(db, claim)

    while True:
        try:
            claim = await run_in_threadpool(claim_preview, runtime)
            if claim:
                work = asyncio.create_task(run_in_threadpool(planner.process, runtime, claim))
                try:
                    while not work.done():
                        done, _ = await asyncio.wait({work}, timeout=5)
                        if not done:
                            await run_in_threadpool(renew, claim)
                    await work
                finally:
                    if not work.done():
                        work.cancel()
        except Exception as exc:  # noqa: BLE001 - keep the background worker alive
            log.error("background operation=planner error=%s", type(exc).__name__)
        await asyncio.sleep(runtime.settings.maintenance_interval_seconds)


async def loop(runtime, operation, *, numbered=False):
    cycle = 0
    while True:
        try:
            await run_in_threadpool(operation, runtime, *([cycle] if numbered else []))
        except Exception as exc:  # noqa: BLE001 - keep the background worker alive
            log.error("background operation=%s error=%s", operation.__name__, type(exc).__name__)
        cycle += 1
        await asyncio.sleep(runtime.settings.maintenance_interval_seconds)


def create_app(settings=None, *, enable_background=True, planning_provider=provider.invoke):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        engine, sessions = connect(settings)
        runtime = Runtime(
            settings,
            sessions,
            Storage(settings.storage_dir),
            Catalog(settings.catalog_file),
            planning_provider,
        )
        app.state.runtime = runtime
        background = []
        try:
            if enable_background:
                background = [
                    asyncio.create_task(loop(runtime, maintenance, numbered=True)),
                    asyncio.create_task(loop(runtime, verify_once)),
                    asyncio.create_task(plan_loop(runtime)),
                ]
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            engine.dispose()

    app = FastAPI(
        title="Campus Edge Fabric Control Plane",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url=f"{Prefix}/openapi.json",
        docs_url=f"{Prefix}/docs",
        redoc_url=None,
    )
    app.add_middleware(RequestLimit)

    @app.exception_handler(Problem)
    def problem_handler(request, exc):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    def validation_handler(request, exc):
        fields = [{"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in exc.errors()]
        return error_response(
            request, Problem("INVALID_REQUEST", "Request validation failed", 422), fields
        )

    @app.exception_handler(HTTPException)
    def http_handler(request, exc):
        return error_response(
            request,
            Problem(
                "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                "Request could not be processed",
                exc.status_code,
            ),
        )

    @app.exception_handler(OperationalError)
    def database_handler(request, exc):
        return error_response(
            request, Problem("DATABASE_UNAVAILABLE", "Database is unavailable", 503, retryable=True)
        )

    @app.get(f"{Prefix}/health", operation_id="health")
    def health(db: DBDep) -> dict[str, str]:
        db.execute(text("SELECT 1 FROM alembic_version"))
        return {"status": "ok"}

    @app.get(f"{Prefix}/protocol", operation_id="protocol")
    def protocol() -> dto.Protocol:
        return dto.Protocol(
            lease_ms=settings.lease_ms,
            heartbeat_interval_ms=settings.heartbeat_interval_ms,
            long_poll_ms=settings.long_poll_ms,
            tls_fingerprint_sha256=settings.tls_fingerprint_sha256,
        )

    @app.post(f"{Prefix}/invitations", status_code=201, operation_id="create_invitation")
    def invite(body: dto.InviteCreate, ctx: ContextDep) -> dto.Invitation:
        user = ctx.actor.member()
        if body.kind == "member":
            ctx.actor.admin()
        return auth.create_invite(ctx.db, user, body)

    @app.post(f"{Prefix}/invitations/redeem", status_code=201, operation_id="redeem_device")
    def redeem(body: dto.DeviceRedeem, db: DBDep) -> dto.Credential:
        return auth.redeem(db, body)

    def browser_session(response, token, csrf):
        for name, value, httponly in ((auth.COOKIE, token, True), (auth.CSRF_COOKIE, csrf, False)):
            response.set_cookie(
                name,
                value,
                max_age=43_200,
                secure=settings.secure_cookies,
                httponly=httponly,
                samesite="strict",
            )

    @app.post(f"{Prefix}/auth/register", status_code=201, operation_id="register")
    def register(
        body: dto.AccountCreate, db: DBDep, request: Request, response: Response
    ) -> dto.Identity:
        if request.headers.get("origin") != settings.public_origin:
            raise Problem("CSRF_REQUIRED", "A matching origin is required", 403)
        identity, token, csrf = auth.register(db, body, settings)
        browser_session(response, token, csrf)
        return identity

    @app.post(f"{Prefix}/auth/account", operation_id="activate_account")
    def account(body: dto.AccountCreate, ctx: ContextDep, request: Request) -> dto.Identity:
        user = auth.set_account(ctx.db, ctx.actor.member(), body)
        csrf = request.cookies.get(auth.CSRF_COOKIE) if ctx.actor.credential.scope == "browser" else None
        return auth.identity(user, csrf)

    @app.post(f"{Prefix}/auth/login", operation_id="login")
    def login(
        body: dto.InviteLogin | dto.PasswordLogin, db: DBDep, request: Request, response: Response
    ) -> dto.Identity:
        if request.headers.get("origin") != settings.public_origin:
            raise Problem("CSRF_REQUIRED", "A matching origin is required", 403)
        identity, token, csrf = auth.login(db, body)
        browser_session(response, token, csrf)
        return identity

    @app.post(f"{Prefix}/auth/logout", status_code=204, operation_id="logout")
    def logout(ctx: ContextDep, response: Response):
        ctx.actor.member()
        auth.revoke(ctx.db, ctx.actor, ctx.actor.credential.id)
        response.delete_cookie(auth.COOKIE)
        response.delete_cookie(auth.CSRF_COOKIE)

    @app.post(f"{Prefix}/auth/cli-login", operation_id="cli_login")
    def cli_login(body: dto.PasswordLogin, db: DBDep, request: Request) -> dto.CLILoginResult:
        origin = request.headers.get("origin")
        if origin is not None and origin != settings.public_origin:
            raise Problem("CSRF_REQUIRED", "The request origin is not allowed", 403)
        return auth.cli_login(db, body)

    @app.post(f"{Prefix}/auth/cli-logout", status_code=204, operation_id="cli_logout")
    def cli_logout(ctx: ContextDep):
        if ctx.actor.credential.scope != "cli":
            raise auth.forbidden()
        auth.revoke(ctx.db, ctx.actor, ctx.actor.credential.id)

    @app.get(f"{Prefix}/auth/me", operation_id="me")
    def me(ctx: ContextDep, request: Request) -> dto.Identity:
        csrf = request.cookies.get(auth.CSRF_COOKIE) if ctx.actor.credential.scope == "browser" else None
        return auth.identity(ctx.actor.member(), csrf)

    @app.post(f"{Prefix}/auth/cli-credentials", status_code=201, operation_id="create_cli_credential")
    def issue_cli(ctx: ContextDep) -> dto.Credential:
        if ctx.actor.credential.scope != "browser":
            raise auth.forbidden()
        return auth.credential_response(auth.issue_credential(ctx.db, ctx.actor.member(), "cli"))

    @app.delete(
        f"{Prefix}/credentials/{{credential_id}}", status_code=204, operation_id="revoke_credential"
    )
    def revoke(credential_id: UUID, ctx: ContextDep):
        auth.revoke(ctx.db, ctx.actor, credential_id)

    @app.post(f"{Prefix}/node-sessions", status_code=201, operation_id="join_node")
    def join(body: dto.SessionCreate, ctx: ContextDep) -> dto.NodeSession:
        return auth.join(ctx.db, ctx.actor, body, settings)

    @app.put(f"{Prefix}/node-sessions/{{session_id}}/capabilities", operation_id="put_capabilities")
    def capabilities(session_id: UUID, body: dto.Capabilities, ctx: ContextDep) -> dto.NodeSession:
        return auth.set_capabilities(ctx.db, ctx.actor, session_id, body, settings)

    @app.post(
        f"{Prefix}/node-sessions/{{session_id}}/telemetry",
        status_code=204,
        operation_id="node_telemetry",
    )
    def node_telemetry(session_id: UUID, body: dto.Telemetry, ctx: ContextDep):
        observability.append_metric(ctx.db, ctx.actor, session_id, body)

    @app.get(f"{Prefix}/recipes", operation_id="list_recipes")
    def recipes(ctx: ContextDep) -> list[dto.Recipe]:
        ctx.actor.member()
        return list(ctx.runtime.catalog.recipes.values())

    @app.get(f"{Prefix}/recipes/{{recipe_id}}", operation_id="get_recipe")
    def recipe(recipe_id: str, ctx: ContextDep) -> dto.Recipe:
        ctx.actor.member()
        row = ctx.runtime.catalog.recipes.get(recipe_id)
        if not row:
            raise missing()
        return row

    @app.get(f"{Prefix}/environments", operation_id="list_environments")
    def environments(ctx: ContextDep) -> list[dto.Environment]:
        ctx.actor.member()
        return list(ctx.runtime.catalog.environments.values())

    @app.post(f"{Prefix}/job-previews", status_code=202, operation_id="create_preview")
    def create_preview(body: dto.PreviewRequest, ctx: ContextDep) -> dto.Preview:
        return planner.plan_job(ctx.db, ctx.runtime, ctx.actor, body)

    @app.get(f"{Prefix}/job-previews/{{preview_id}}", operation_id="get_preview")
    def get_preview(preview_id: UUID, ctx: ContextDep) -> dto.Preview:
        return planner.describe(jobs.owned_preview(ctx.db, ctx.actor, preview_id))

    @app.post(
        f"{Prefix}/job-previews/{{preview_id}}/answers", status_code=202, operation_id="answer_preview"
    )
    def answer(preview_id: UUID, body: dto.PreviewAnswer, ctx: ContextDep) -> dto.Preview:
        return planner.answer(ctx.db, ctx.runtime, ctx.actor, preview_id, body)

    @app.post(f"{Prefix}/job-previews/{{preview_id}}/cancel", operation_id="cancel_preview")
    def cancel_preview(preview_id: UUID, ctx: ContextDep) -> dto.Preview:
        return planner.cancel_preview(ctx.db, ctx.actor, preview_id)

    @app.post(f"{Prefix}/jobs", status_code=201, operation_id="create_job")
    def create_job(
        body: dto.JobCreate,
        ctx: ContextDep,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[ -~]+$")],
    ) -> dto.Job:
        return jobs.describe(
            ctx.db, jobs.start_job(ctx.db, ctx.runtime, ctx.actor, body, idempotency_key)
        )

    @app.get(f"{Prefix}/jobs", operation_id="list_jobs")
    def list_jobs(ctx: ContextDep, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> list[dto.Job]:
        user = ctx.actor.member()
        query = select(m.Job).where(m.Job.pool_id == user.pool_id, m.Job.purpose != "probe")
        if not ctx.actor.privileged:
            query = query.where(m.Job.owner_id == user.id)
        return [
            jobs.describe(ctx.db, row)
            for row in ctx.db.scalars(query.order_by(m.Job.created_at.desc()).limit(limit))
        ]

    @app.get(f"{Prefix}/jobs/{{job_id}}", operation_id="get_job")
    def get_job(job_id: UUID, ctx: ContextDep) -> dto.Job:
        return jobs.describe(ctx.db, jobs.owned_job(ctx.db, ctx.actor, job_id))

    @app.post(f"{Prefix}/jobs/{{job_id}}/cancel", operation_id="cancel_job")
    def cancel_job(job_id: UUID, ctx: ContextDep) -> dto.Job:
        job = jobs.owned_job(ctx.db, ctx.actor, job_id)
        jobs.cancel(ctx.db, job)
        return jobs.describe(ctx.db, job)

    @app.get(f"{Prefix}/jobs/{{job_id}}/logs", operation_id="job_logs")
    def logs(job_id: UUID, ctx: ContextDep, cursor: Cursor = 0) -> dto.LogPage:
        return observability.logs(ctx.db, ctx.actor, job_id, cursor)

    @app.post(f"{Prefix}/jobs/{{job_id}}/search", status_code=202, operation_id="search_job")
    def start_search(job_id: UUID, body: dto.SearchRequest, ctx: ContextDep) -> dto.SearchStart:
        result = search.start_search(ctx.db, ctx.runtime, ctx.actor, job_id, body)
        return dto.SearchStart(search_job_id=result.id)

    def event_view(request, cursor, entity_id, kind):
        runtime = metadata(request)
        last = request.headers.get("last-event-id")
        if last is not None:
            if not re.fullmatch(r"[0-9]{1,19}", last) or int(last) >= 2**63:
                raise Problem("INVALID_CURSOR", "Last-Event-ID is invalid", 422)
            cursor = int(last)

        def read(value):
            with runtime.sessions() as db:
                actor = auth.authenticate(db, request, settings)
                user = actor.member()
                if kind == "job":
                    jobs.owned_job(db, actor, entity_id)
                elif kind == "preview":
                    jobs.owned_preview(db, actor, entity_id)
                elif not actor.privileged:
                    raise auth.forbidden()
                return events.page(db, settings, user.pool_id, value, entity_id)

        first = read(cursor)
        if "text/event-stream" not in request.headers.get("accept", ""):
            return first

        async def stream():
            page = first
            for _ in range(26):
                if await request.is_disconnected():
                    break
                if page.reset:
                    yield f"id: {page.cursor}\nevent: reset\ndata: {{}}\n\n"
                    break
                for item in page.events:
                    yield f"id: {item.event_id}\ndata: {item.model_dump_json()}\n\n"
                if not page.events:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
                try:
                    page = await run_in_threadpool(read, page.cursor)
                except Problem:
                    break

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get(f"{Prefix}/job-previews/{{preview_id}}/events", operation_id="preview_events")
    def preview_events(request: Request, preview_id: UUID, cursor: Cursor = 0) -> dto.EventPage:
        return event_view(request, cursor, preview_id, "preview")

    @app.get(f"{Prefix}/jobs/{{job_id}}/events", operation_id="job_events")
    def job_events(request: Request, job_id: UUID, cursor: Cursor = 0) -> dto.EventPage:
        return event_view(request, cursor, job_id, "job")

    @app.get(f"{Prefix}/pool/events", operation_id="pool_events")
    def pool_events(request: Request, cursor: Cursor = 0) -> dto.EventPage:
        return event_view(request, cursor, None, "pool")

    @app.post(
        f"{Prefix}/work/next",
        responses={204: {"description": "No compatible assignment"}},
        operation_id="claim_work",
    )
    async def work(request: Request, body: dto.WorkRequest) -> dto.Assignment | dto.NativeAssignment:
        runtime = metadata(request)
        session_id = session_header(request)

        def claim():
            with runtime.sessions.begin() as db:
                write_lock(db)
                actor = auth.authenticate(db, request, settings)
                native = request.headers.get("x-campus-worker-protocol") == "2"
                policy = (
                    None
                    if native
                    else worker_compat.policy_for(actor, request.headers.get("user-agent"))
                )
                return scheduler.claim_work(
                    db, runtime, actor, session_id, worker_policy=policy, native=native
                )

        deadline = time.monotonic() + min(body.wait_ms, settings.long_poll_ms) / 1000
        while True:
            assignment = await run_in_threadpool(claim)
            if assignment:
                return JSONResponse(assignment) if isinstance(assignment, dict) else assignment
            remaining = deadline - time.monotonic()
            if remaining <= 0 or await request.is_disconnected():
                return Response(status_code=204)
            await asyncio.sleep(min(0.5, remaining))

    @app.get(f"{Prefix}/attempts/{{attempt_id}}", operation_id="attempt_status")
    def attempt_status(attempt_id: UUID, ctx: ContextDep) -> dto.AttemptStatus:
        return leases.attempt_status(ctx.db, ctx.actor, ctx.session_id, attempt_id)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/started", operation_id="attempt_started")
    def attempt_started(attempt_id: UUID, body: dto.AttemptProof, ctx: ContextDep) -> dto.LeaseReceipt:
        return leases.started(ctx.db, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/renew", operation_id="renew_attempt")
    def renew_attempt(attempt_id: UUID, body: dto.AttemptProof, ctx: ContextDep) -> dto.LeaseReceipt:
        return leases.renew_attempt(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(
        f"{Prefix}/attempts/{{attempt_id}}/complete", status_code=202, operation_id="complete_attempt"
    )
    def complete_attempt(
        request: Request, attempt_id: UUID, body: dto.Complete, ctx: ContextDep
    ) -> dto.CompletionReceipt:
        policy = (
            None
            if request.headers.get("x-campus-worker-protocol") == "2"
            else (worker_compat.policy_for(ctx.actor, request.headers.get("user-agent")))
        )
        if policy:
            try:
                manifest = worker_compat.result_manifest(payload(body.manifest), policy)
            except ValueError as exc:
                raise Problem("INVALID_RESULT", str(exc), 422) from exc
            body = body.model_copy(update={"manifest": dto.ResultManifest.model_validate(manifest)})
        return leases.complete_attempt(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/fail", operation_id="fail_attempt")
    def fail_attempt(attempt_id: UUID, body: dto.Failure, ctx: ContextDep) -> dto.AttemptStatus:
        return leases.fail(ctx.db, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/release", operation_id="release_attempt")
    def release_attempt(attempt_id: UUID, body: dto.AttemptProof, ctx: ContextDep) -> dto.AttemptStatus:
        return leases.fail(ctx.db, ctx.actor, ctx.session_id, attempt_id, body, release=True)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/checkpoints", operation_id="publish_checkpoint")
    def checkpoint(attempt_id: UUID, body: dto.CheckpointPublish, ctx: ContextDep) -> dto.Artifact:
        return leases.checkpoint(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(f"{Prefix}/attempts/{{attempt_id}}/logs", status_code=204, operation_id="append_log")
    def append_log(attempt_id: UUID, body: dto.LogWrite, ctx: ContextDep):
        observability.append_log(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, attempt_id, body)

    @app.post(
        f"{Prefix}/attempts/{{attempt_id}}/telemetry", status_code=204, operation_id="attempt_telemetry"
    )
    def attempt_telemetry(attempt_id: UUID, body: dto.AttemptTelemetry, ctx: ContextDep):
        observability.append_metric(ctx.db, ctx.actor, ctx.session_id, body, attempt_id)

    @app.post(f"{Prefix}/uploads", status_code=201, operation_id="create_upload")
    def upload(body: dto.UploadCreate, ctx: ContextDep) -> dto.Upload:
        return uploads.begin(ctx.db, ctx.runtime, ctx.actor, ctx.session_id, body)

    @app.get(f"{Prefix}/uploads/{{upload_id}}", operation_id="upload_status")
    def upload_status(upload_id: UUID, ctx: ContextDep) -> dto.Upload:
        return uploads.describe(ctx.db, uploads.owned(ctx.db, ctx.actor, ctx.session_id, upload_id))

    @app.put(
        f"{Prefix}/uploads/{{upload_id}}/parts/{{part}}",
        operation_id="put_upload_part",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    async def upload_part(
        request: Request,
        upload_id: UUID,
        part: int,
        x_content_sha256: Annotated[str, Header(pattern=r"^[0-9a-f]{64}$")],
    ) -> dto.UploadPart:
        runtime = metadata(request)
        session_id = session_header(request)

        def check():
            with runtime.sessions() as db:
                actor = auth.authenticate(db, request, settings)
                return uploads.check_part(db, actor, session_id, upload_id, part, x_content_sha256)

        size, exists = await run_in_threadpool(check)
        if exists:
            return dto.UploadPart(number=part, sha256=x_content_sha256, size_bytes=size)
        chunks = []
        length = 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > size:
                raise Problem("INVALID_SIZE", "Part exceeds the expected size", 422)
            chunks.append(chunk)

        def publish_part():
            runtime.storage.publish(
                runtime.storage.part(upload_id, f"{part}-{x_content_sha256}"),
                chunks,
                x_content_sha256,
                size,
            )
            with runtime.sessions.begin() as db:
                write_lock(db)
                actor = auth.authenticate(db, request, settings)
                return uploads.register_part(
                    db, runtime, actor, session_id, upload_id, part, x_content_sha256, size
                )

        return await run_in_threadpool(publish_part)

    @app.post(f"{Prefix}/uploads/{{upload_id}}/finalize", operation_id="finalize_upload")
    def finalize(request: Request, upload_id: UUID) -> dto.Artifact:
        runtime = metadata(request)
        session_id = session_header(request)
        with runtime.sessions() as db:
            actor = auth.authenticate(db, request, settings)
            row, view = uploads.finalize_info(db, actor, session_id, upload_id)
        if view.artifact:
            return view.artifact
        uploads.publish_blob(runtime, row, view)
        with runtime.sessions.begin() as db:
            write_lock(db)
            actor = auth.authenticate(db, request, settings)
            return uploads.register_artifact(db, runtime, actor, session_id, upload_id)

    @app.get(f"{Prefix}/artifacts/lookup", operation_id="lookup_owned_artifact")
    def lookup_owned_artifact(
        ctx: ContextDep,
        sha256: dto.SHA256,
        size_bytes: Annotated[int, Query(ge=0)],
        kind: Literal["input", "project"] = "input",
    ) -> dto.Artifact:
        return uploads.lookup_owned(ctx.db, ctx.runtime, ctx.actor, sha256, size_bytes, kind)

    @app.get(
        f"{Prefix}/artifacts/{{artifact_id}}/content",
        operation_id="download_artifact",
        response_class=FileResponse,
    )
    def download(artifact_id: UUID, ctx: ContextDep):
        row = uploads.resolve_download(ctx.db, ctx.actor, ctx.session_id, artifact_id)
        path = ctx.runtime.storage.blob(row.sha256)
        if not path.is_file():
            raise Problem("STORAGE_UNAVAILABLE", "Artifact file is unavailable", 503, retryable=True)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=Path(row.name).name,
            headers={"X-Content-SHA256": row.sha256, "ETag": f'"{row.sha256}"'},
        )

    @app.get(f"{Prefix}/nodes", operation_id="list_nodes")
    def nodes(ctx: ContextDep) -> list[dto.Node]:
        user = ctx.actor.member()
        current = now(ctx.db)
        query = select(m.Node).where(m.Node.pool_id == user.pool_id)
        if not ctx.actor.privileged:
            query = query.where(m.Node.owner_id == user.id)
        return [
            observability.describe_node(ctx.db, ctx.runtime, node, current)
            for node in ctx.db.scalars(query.order_by(m.Node.created_at))
        ]

    @app.get(f"{Prefix}/nodes/{{node_id}}", operation_id="get_node")
    def get_node(node_id: UUID, ctx: ContextDep) -> dto.Node:
        return observability.describe_node(
            ctx.db, ctx.runtime, observability.node_in_pool(ctx.db, ctx.actor, node_id), now(ctx.db)
        )

    @app.post(f"{Prefix}/nodes/{{node_id}}/control", operation_id="control_node")
    def control_node(node_id: UUID, body: dto.NodeControl, ctx: ContextDep) -> dto.Node:
        return observability.describe_node(
            ctx.db, ctx.runtime, observability.control(ctx.db, ctx.actor, node_id, body), now(ctx.db)
        )

    @app.get(f"{Prefix}/device/settings", operation_id="get_device_settings")
    def get_device_settings(ctx: ContextDep) -> dto.DeviceSettings:
        return device_settings.read(ctx.db, ctx.actor)

    @app.post(f"{Prefix}/device/settings/ack", operation_id="ack_device_settings")
    def ack_device_settings(body: dto.SettingsAck, ctx: ContextDep) -> dto.DeviceSettings:
        return device_settings.acknowledge(ctx.db, ctx.actor, body)

    @app.patch(f"{Prefix}/nodes/{{node_id}}/settings", operation_id="set_device_settings")
    def set_device_settings(node_id: UUID, body: dto.SettingsPatch, ctx: ContextDep) -> dto.Node:
        node = observability.node_in_pool(ctx.db, ctx.actor, node_id)
        return observability.describe_node(
            ctx.db, ctx.runtime, device_settings.update(ctx.db, node, body), now(ctx.db)
        )

    @app.get(f"{Prefix}/me/stats", operation_id="user_stats")
    def user_stats(ctx: ContextDep) -> dto.UserStats:
        return observability.user_stats(ctx.db, ctx.runtime, ctx.actor)

    @app.get(f"{Prefix}/admin/users", operation_id="admin_users")
    def admin_users(ctx: ContextDep) -> list[dto.Identity]:
        ctx.actor.admin()
        return [
            auth.identity(user)
            for user in ctx.db.scalars(
                select(m.User)
                .where(m.User.pool_id == ctx.actor.user.pool_id)
                .order_by(m.User.name)
                .limit(500)
            )
        ]

    @app.get(f"{Prefix}/pool/summary", operation_id="pool_summary")
    def pool(ctx: ContextDep) -> dto.PoolSummary:
        return observability.summary(ctx.db, ctx.runtime, ctx.actor)

    @app.get(f"{Prefix}/credits", operation_id="list_credits")
    def credits(ctx: ContextDep) -> list[dto.Credit]:
        return observability.credits(ctx.db, ctx.actor)

    @app.post(f"{Prefix}/benchmarks", status_code=201, operation_id="create_benchmark")
    def create_benchmark(body: dto.BenchmarkCreate, ctx: ContextDep) -> dto.BenchmarkReport:
        return benchmarks.report(ctx.db, benchmarks.create(ctx.db, ctx.runtime, ctx.actor, body))

    @app.post(
        f"{Prefix}/benchmarks/{{benchmark_id}}/standalone-results",
        status_code=204,
        operation_id="submit_standalone",
    )
    def standalone(benchmark_id: UUID, body: dto.StandaloneRecord, ctx: ContextDep):
        benchmarks.standalone(ctx.db, ctx.actor, benchmark_id, body)

    @app.get(f"{Prefix}/benchmarks", operation_id="list_benchmarks")
    def list_benchmarks(ctx: ContextDep) -> list[dto.BenchmarkReport]:
        ctx.actor.admin()
        user = ctx.actor.member()
        return [
            benchmarks.report(ctx.db, row)
            for row in ctx.db.scalars(
                select(m.Benchmark)
                .where(m.Benchmark.pool_id == user.pool_id)
                .order_by(m.Benchmark.created_at.desc())
                .limit(30)
            )
        ]

    @app.get(f"{Prefix}/benchmarks/{{benchmark_id}}", operation_id="get_benchmark")
    def get_benchmark(benchmark_id: UUID, ctx: ContextDep) -> dto.BenchmarkReport:
        ctx.actor.admin()
        return benchmarks.report(ctx.db, benchmarks.owned(ctx.db, ctx.actor, benchmark_id))

    @app.get(f"{Prefix}/benchmarks/{{benchmark_id}}/export", operation_id="export_benchmark")
    def export_benchmark(benchmark_id: UUID, ctx: ContextDep) -> dto.BenchmarkReport:
        return get_benchmark(benchmark_id, ctx)

    mount_routes(app, context, Prefix)

    @app.get("/admin", include_in_schema=False)
    def admin_redirect():
        return Response(status_code=308, headers={"Location": "/admin/"})

    @app.get("/admin/{path:path}", include_in_schema=False)
    def console(path: str):
        return spa(settings.console_dist, path)

    @app.get("/{path:path}", include_in_schema=False)
    def portal(path: str):
        if path == "api" or path.startswith("api/"):
            raise missing()
        return spa(settings.portal_dist, path)

    return app


def spa(root, request_path):
    root = root.resolve()
    path = (root / request_path).resolve()
    if not path.is_relative_to(root):
        raise missing()
    if path.is_file():
        return FileResponse(path)
    if not Path(request_path).suffix and (root / "index.html").is_file():
        return FileResponse(root / "index.html")
    raise missing()


app = create_app()
