import argparse
import json

from sqlalchemy import select

from . import dto
from . import models as m
from .artifacts.storage import Storage
from .artifacts.uploads import reconcile
from .auth import create_invite, issue_credential
from .config import Settings
from .db import connect, payload, write_lock


def initialize(db, pool_name, owner_name):
    write_lock(db)
    existing = db.scalar(select(m.Pool).limit(1))
    if existing:
        raise SystemExit("A pool already exists. Use owner-login to reissue an invitation.")
    pool = m.Pool(name=pool_name)
    db.add(pool)
    db.flush()
    user = m.User(pool_id=pool.id, name=owner_name, role="owner")
    db.add(user)
    db.flush()
    return user


def get_owner(db):
    owner = db.scalar(select(m.User).where(m.User.role == "owner"))
    if not owner:
        raise SystemExit("Initialize the pool first.")
    return owner


def owner_invite(db, owner):
    return create_invite(
        db,
        owner,
        dto.InviteCreate(kind="member", name=owner.name, ttl_seconds=900),
        existing_user=owner.id,
    )


def main():
    parser = argparse.ArgumentParser(description="Local coordinator administration")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "initialize", help="Create the pool and issue the first owner invitation"
    )
    bootstrap.add_argument("--pool", default="Campus")
    bootstrap.add_argument("--owner", default="Pool owner")
    commands.add_parser(
        "owner-login", help="Issue a 15-minute one-time invitation to the existing owner"
    )
    commands.add_parser("owner-cli", help="Issue a 30-day local administrative CLI credential")
    commands.add_parser("reconcile", help="Check that every published artifact exists")
    args = parser.parse_args()
    settings = Settings()
    engine, sessions = connect(settings)
    try:
        with sessions.begin() as db:
            if args.command == "reconcile":
                from types import SimpleNamespace

                result = reconcile(db, SimpleNamespace(storage=Storage(settings.storage_dir)))
            elif args.command == "owner-cli":
                row, token, _ = issue_credential(db, get_owner(db), "cli")
                result = payload(
                    dto.Credential(id=row.id, token=token, scope="cli", expires_at=row.expires_at)
                )
            else:
                user = (
                    initialize(db, args.pool, args.owner)
                    if args.command == "initialize"
                    else get_owner(db)
                )
                result = payload(owner_invite(db, user))
        print(json.dumps(result, ensure_ascii=False))
        if result.get("missing_blobs"):
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
