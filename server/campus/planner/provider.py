import json
import logging
import time
from typing import Annotated

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
    model_validator,
)

from .. import dto
from ..db import digest
from ..errors import Problem
from .prompts import REVISION, SYSTEM

log = logging.getLogger(__name__)


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: dto.Identifier
    adapter_version: dto.Identifier


class ProjectReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
    warnings: list[dto.ShortText] = Field(default_factory=list, max_length=24)
    evidence: list[dto.Evidence] = Field(default_factory=list, max_length=64)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_review: ProjectReview | None = None
    question: dto.Question | None = None


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dto.JobPlan | None = None
    question: dto.Question | None = None
    probe: Probe | None = None
    project_review: ProjectReview | None = None

    @model_validator(mode="after")
    def one_action(self):
        if (
            sum(item is not None for item in (self.plan, self.question, self.probe, self.project_review))
            != 1
        ):
            raise ValueError("Expected exactly one planning action")
        return self


def invoke(settings, context, budget_seconds):
    if (
        settings.llm_provider not in {"model-studio", "openai-compatible", "deepseek"}
        or not settings.llm_api_key
        or not settings.llm_model
        or not settings.llm_base_url
    ):
        raise Problem(
            "PLANNER_UNAVAILABLE", "The planning provider is not configured", 503, retryable=True
        )
    url = httpx.URL(settings.llm_base_url.rstrip("/") + "/chat/completions")
    if (
        url.scheme != "https"
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise Problem("PLANNER_UNAVAILABLE", "Planning provider requires a valid HTTPS base URL", 503)
    project_request = "project_config" in context
    response_format = {"type": "json_object"}
    if settings.llm_json_mode == "json_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "campus_plan",
                "schema": (ProjectResponse if project_request else Proposal).model_json_schema(),
            },
        }
    schema_context = (
        {
            "project_response_schema": ProjectResponse.model_json_schema(),
        }
        if project_request
        else {"job_plan_schema": dto.JobPlan.model_json_schema()}
    )
    data = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {**context, **schema_context},
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": response_format,
        "max_tokens": 4096,
        "stream": False,
    }
    if settings.llm_provider == "deepseek":
        data["thinking"] = {"type": "disabled"}
    deadline = time.monotonic() + budget_seconds
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Problem("PLANNER_TIMEOUT", "Planning deadline passed", 503, retryable=True)
        try:
            with (
                httpx.Client(
                    timeout=min(remaining, settings.llm_timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    url,
                    json=data,
                    headers={
                        "Authorization": "Bearer " + settings.llm_api_key.get_secret_value(),
                    },
                ) as response,
            ):
                status = response.status_code
                raw = bytearray()
                if status < 300:
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 262_144 or time.monotonic() > deadline:
                            raise Problem(
                                "PLANNER_INCOMPLETE", "Planner response exceeded its budget", 502
                            )
            if status == 429:
                problem = Problem(
                    "PLANNER_RATE_LIMIT", "The provider rate limit was reached", 503, retryable=True
                )
            elif status >= 500:
                problem = Problem(
                    "PLANNER_UNAVAILABLE", "The planning provider is unavailable", 503, retryable=True
                )
            elif status >= 300:
                raise Problem(
                    "PLANNER_PROVIDER_ERROR", "The provider rejected planning configuration", 502
                )
            else:
                try:
                    return parse_response(settings, raw, context)
                except Problem as exc:
                    if not project_request or exc.code not in {
                        "PLANNER_SCHEMA_INVALID",
                        "PLANNER_INCOMPLETE",
                    }:
                        raise
                    problem = exc
                    # Repair only the response shape, within the same bounded
                    # request budget. No malformed action is ever executed.
                    data["messages"] = data["messages"][:2] + [
                        {
                            "role": "user",
                            "content": "The previous response did not match the supplied JSON schema. Return exactly one project_review or question object. Follow the field types, string lengths and array bounds exactly; approved must be a JSON boolean. Do not include other fields.",
                        }
                    ]
        except httpx.TimeoutException:
            problem = Problem("PLANNER_TIMEOUT", "The planning provider timed out", 503, retryable=True)
        except httpx.TransportError:
            problem = Problem(
                "PLANNER_UNAVAILABLE", "The planning provider could not be reached", 503, retryable=True
            )
        if attempt == 2 or deadline - time.monotonic() <= 0.2 * 2**attempt:
            raise problem
        time.sleep(0.2 * 2**attempt)


def parse_response(settings, raw, context):
    try:
        response = json.loads(raw)
        choice = response["choices"][0]
        message = choice["message"]
        if message.get("refusal") or choice.get("finish_reason") == "content_filter":
            raise Problem("PLANNER_REFUSED", "The planning provider refused this request", 422)
        if choice.get("finish_reason") != "stop":
            raise Problem(
                "PLANNER_INCOMPLETE", "The planning provider returned an incomplete result", 502
            )
        usage = response.get("usage") or {}
        log.info(
            "planner provider=%s model=%s revision=%s input_digest=%s input_tokens=%s output_tokens=%s",
            settings.llm_provider,
            settings.llm_model,
            REVISION,
            digest(context),
            usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
            usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
        )
        body = json.loads(message["content"])
        if "project_config" in context and isinstance(body, dict) and "approved" in body:
            # Some JSON-mode providers return the inner review directly. Its
            # strict type/field bounds are identical; only the envelope changes.
            return Proposal(project_review=ProjectReview.model_validate(body))
        return Proposal.model_validate(body)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError) as exc:
        raise Problem(
            "PLANNER_SCHEMA_INVALID", "The provider response did not match the planning schema", 422
        ) from exc
