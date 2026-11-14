"""Local fixture API. Select a deterministic scenario with CAMPUS_FIXTURE."""

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent
api = json.loads((ROOT / "openapi-v1.yaml").read_text())
available = {p.stem: p for p in (ROOT / "fixtures").glob("*.json")}
scenario = os.environ.get("CAMPUS_FIXTURE", "ready")
if scenario not in available:
    raise RuntimeError(
        f"Unknown scenario {scenario!r}. Available: {', '.join(sorted(available))}"
    )
responses = json.loads(available[scenario].read_text())["responses"]
app = FastAPI(
    title="Campus v1 fixture API",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
)
app.openapi = lambda: api


def handler(operation):
    async def respond(request: Request):
        reply = responses.get(operation)
        if reply is None:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "FIXTURE_UNAVAILABLE",
                    "message": f"No fixture for {operation}",
                    "field_errors": [],
                    "request_id": "fixture",
                    "retryable": False,
                },
            )
        if reply.get("status") == 204:
            return Response(status_code=204)
        body = reply["body"]
        if "text/event-stream" in request.headers.get(
            "accept", ""
        ) and operation.endswith("events"):
            cursor = int(
                request.headers.get(
                    "last-event-id", request.query_params.get("cursor", "0")
                )
            )
            data = ""
            if body["reset"]:
                data = f"id: {body['cursor']}\nevent: reset\ndata: {{}}\n\n"
            for event in body["events"]:
                if event["event_id"] > cursor:
                    data += f"id: {event['event_id']}\ndata: {json.dumps(event)}\n\n"
            return Response(data or ": heartbeat\n\n", media_type="text/event-stream")
        return JSONResponse(body, status_code=reply.get("status", 200))

    return respond


for path, methods in api["paths"].items():
    for verb, operation in methods.items():
        app.add_api_route(
            path, handler(operation["operationId"]), methods=[verb.upper()]
        )
