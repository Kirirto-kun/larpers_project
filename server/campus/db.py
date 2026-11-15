import hashlib
import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


def connect(settings):
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=12, max_overflow=20)
    return engine, sessionmaker(engine, expire_on_commit=False)


def now(db):
    return db.scalar(select(func.clock_timestamp()))


def write_lock(db):
    # Campus deployment uses a single coordinator. Keep state writes short and ordered.
    # A transaction-level DB lock also serializes writers across API processes.
    db.execute(select(func.pg_advisory_xact_lock(513277119)))


def payload(value):
    return value.model_dump(mode="json")


def digest(value):
    if hasattr(value, "model_dump"):
        value = payload(value)
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def secret_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()
