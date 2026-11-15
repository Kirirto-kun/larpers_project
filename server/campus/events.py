from sqlalchemy import select

from . import dto, models


def emit(db, pool_id, entity_type, entity_id, kind, **data):
    db.add(
        models.Event(
            pool_id=pool_id, entity_type=entity_type, entity_id=entity_id, type=kind, payload=data
        )
    )


def page(db, settings, pool_id, cursor, entity_id=None):
    query = select(models.Event).where(models.Event.pool_id == pool_id)
    if entity_id is not None:
        query = query.where(models.Event.entity_id == entity_id)
    recent = list(
        db.scalars(query.order_by(models.Event.id.desc()).limit(settings.event_replay_limit + 1))
    )
    if not recent:
        return dto.EventPage(events=[], cursor=0, reset=cursor != 0)
    newest = recent[0].id
    if cursor > newest or (len(recent) > settings.event_replay_limit and cursor < recent[-1].id):
        return dto.EventPage(events=[], cursor=newest, reset=True)
    events = [
        dto.Event(
            event_id=e.id,
            occurred_at=e.created_at,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            type=e.type,
            payload=e.payload,
        )
        for e in reversed(recent)
        if e.id > cursor
    ]
    return dto.EventPage(events=events, cursor=newest)
