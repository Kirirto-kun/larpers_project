"""Owner-scoped receipts for actual successful project reviews.

Only an opaque receipt key is written to pool events. Review text stays in the
owner's private preview plan; this cache never publishes code or provider data.
"""

from sqlalchemy import select

from .. import models as m
from ..db import digest
from .prompts import REVISION
from .provider import ProjectReview


def review_key(settings, context):
    return digest(
        {
            "project": context["project_digest"],
            "inputs": context["input_manifest_digest"],
            "config": context["project_config"],
            "selected_files": context["selected_files"],
            "prompt": context["prompt"],
            "answer": context["answer"],
            "provider": settings.llm_provider,
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "prompt_revision": REVISION,
        }
    )


def cached_review(db, user, key):
    row = db.scalar(
        select(m.Preview)
        .join(m.Event, m.Event.entity_id == m.Preview.id)
        .where(
            m.Preview.owner_id == user.id,
            m.Preview.state == "ready",
            m.Event.pool_id == user.pool_id,
            m.Event.entity_type == "preview",
            m.Event.type == "project_review_accepted",
            m.Event.payload["owner_id"].astext == str(user.id),
            m.Event.payload["cache_key"].astext == key,
        )
        .order_by(m.Event.id.desc())
        .limit(1)
    )
    if row is None or row.plan is None:
        return None
    return ProjectReview(
        approved=True,
        summary="The unchanged project contract and inputs reuse a previous approved review.",
        warnings=row.plan.get("assumptions", [])[1:24],
        evidence=row.plan.get("evidence", [])[:64],
    )
