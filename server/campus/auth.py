import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_bytes, token_urlsafe
from uuid import UUID

from sqlalchemy import func, select

from . import dto
from . import models as m
from .db import now, payload, secret_hash, write_lock
from .errors import Problem, forbidden, missing
from .events import emit

COOKIE = "campus_session"
CSRF_COOKIE = "campus_csrf"


def hash_password(password):
    salt = token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64 << 20)
    return f"scrypt$16384$8$1${salt.hex()}${key.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt, expected = (encoded or "").split("$")
        # Bound all stored parameters before running expensive cryptography.
        if (
            (algorithm, n, r, p) != ("scrypt", "16384", "8", "1")
            or len(salt) != 32
            or len(expected) != 64
        ):
            return False
        key = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32, maxmem=64 << 20
        )
        return hmac.compare_digest(key, bytes.fromhex(expected))
    except (ValueError, TypeError, OverflowError):
        return False


_DUMMY_PASSWORD = hash_password(token_urlsafe(32))


def register(db, body, settings):
    write_lock(db)
    if settings.registration_pool_id:
        pool = db.get(m.Pool, settings.registration_pool_id)
    else:
        pools = list(db.scalars(select(m.Pool).limit(2)))
        pool = pools[0] if len(pools) == 1 else None
    if pool is None:
        raise Problem("REGISTRATION_UNAVAILABLE", "Account registration is not configured", 503)
    user = m.User(pool_id=pool.id, role="member", name=body.name)
    db.add(user)
    set_account(db, user, body)
    _, token, csrf = issue_credential(db, user, "browser")
    return identity(user, csrf), token, csrf


def set_account(db, user, body):
    write_lock(db)
    if user.password_hash or (user.email is not None and user.email.strip().lower() != body.email):
        raise Problem("ACCOUNT_EXISTS", "This account already has sign-in credentials", 409)
    if db.scalar(select(m.User.id).where(m.User.email == body.email, m.User.id != user.id)):
        raise Problem("EMAIL_IN_USE", "This email address is already registered", 409)
    user.name, user.email = body.name, body.email
    user.password_hash = hash_password(body.password)
    db.flush()
    return user


@dataclass
class Actor:
    user: m.User
    credential: m.Credential

    @property
    def privileged(self):
        return self.user.role in {"owner", "admin"}

    def member(self):
        if self.credential.scope == "device":
            raise forbidden()
        return self.user

    def admin(self):
        self.member()
        if not self.privileged:
            raise forbidden()

    def owns(self, owner_id, pool_id=None):
        self.member()
        if (pool_id is not None and self.user.pool_id != pool_id) or (
            self.user.id != owner_id and not self.privileged
        ):
            raise missing()


def authenticate(db, request, settings):
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            raise unauthorized()
    else:
        token = request.cookies.get(COOKIE, "")
    if not 32 <= len(token) <= 160:
        raise unauthorized()
    credential = db.scalar(select(m.Credential).where(m.Credential.token_hash == secret_hash(token)))
    if (
        not credential
        or credential.revoked_at
        or (credential.expires_at and credential.expires_at <= now(db))
    ):
        raise unauthorized()
    if header is not None and credential.scope == "browser":
        raise unauthorized()
    if header is None and credential.scope != "browser":
        raise unauthorized()
    if header is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin") != settings.public_origin
            or not csrf
            or not hmac.compare_digest(secret_hash(csrf), credential.csrf_hash or "")
        ):
            raise Problem("CSRF_REQUIRED", "A matching origin and CSRF token are required", 403)
    return Actor(db.get(m.User, credential.user_id), credential)


def unauthorized():
    return Problem("UNAUTHORIZED", "A valid credential is required", 401)


def create_invite(db, user, body, *, existing_user=None):
    write_lock(db)
    current = now(db)
    count = db.scalar(
        select(func.count())
        .select_from(m.Invite)
        .where(m.Invite.issuer_id == user.id, m.Invite.created_at > current - timedelta(hours=1))
    )
    if count >= 30:
        raise Problem("RATE_LIMITED", "Invitation rate limit exceeded", 429, retryable=True)
    token = token_urlsafe(32)
    row = m.Invite(
        pool_id=user.pool_id,
        issuer_id=user.id,
        name=body.name,
        kind=body.kind,
        existing_user_id=existing_user,
        token_hash=secret_hash(token),
        expires_at=current + timedelta(seconds=body.ttl_seconds),
    )
    db.add(row)
    db.flush()
    return dto.Invitation(id=row.id, token=token, kind=row.kind, expires_at=row.expires_at)


def use_invite(db, token, kind):
    write_lock(db)
    row = db.scalar(select(m.Invite).where(m.Invite.token_hash == secret_hash(token)).with_for_update())
    current = now(db)
    if not row or row.kind != kind or row.consumed_at or row.expires_at <= current:
        raise Problem("INVALID_INVITATION", "Invitation is invalid or has expired", 401)
    row.consumed_at = current
    return row


def issue_credential(db, user, scope, node_id=None):
    token = token_urlsafe(32)
    csrf = token_urlsafe(32) if scope == "browser" else None
    lifetime = {"browser": timedelta(hours=12), "cli": timedelta(days=30)}.get(scope)
    row = m.Credential(
        user_id=user.id,
        node_id=node_id,
        scope=scope,
        token_hash=secret_hash(token),
        csrf_hash=secret_hash(csrf) if csrf else None,
        expires_at=now(db) + lifetime if lifetime else None,
    )
    db.add(row)
    db.flush()
    return row, token, csrf


def identity(user, csrf=None):
    return dto.Identity(
        id=user.id,
        name=user.name,
        role=user.role,
        pool_id=user.pool_id,
        email=user.email,
        csrf_token=csrf,
    )


def login(db, body):
    if isinstance(body, dto.PasswordLogin):
        user = db.scalar(select(m.User).where(m.User.email == body.email.lower()))
        valid = verify_password(body.password, (user.password_hash if user else None) or _DUMMY_PASSWORD)
        if not valid or user is None:
            raise unauthorized()
    else:
        invite = use_invite(db, body.token, "member")
        user = db.get(m.User, invite.existing_user_id) if invite.existing_user_id else None
        if user is None:
            user = m.User(pool_id=invite.pool_id, name=invite.name, role="member")
            db.add(user)
            db.flush()
    _, token, csrf = issue_credential(db, user, "browser")
    return identity(user, csrf), token, csrf


def redeem(db, body):
    invite = use_invite(db, body.token, "device")
    existing = db.scalar(
        select(m.Node).where(
            m.Node.pool_id == invite.pool_id, m.Node.installation_id == body.installation_id
        )
    )
    if existing:
        raise Problem("INSTALLATION_EXISTS", "Use the existing device credential", 409)
    owner = db.get(m.User, invite.issuer_id)
    node = m.Node(
        pool_id=invite.pool_id, owner_id=owner.id, name=invite.name, installation_id=body.installation_id
    )
    db.add(node)
    db.flush()
    return credential_response(issue_credential(db, owner, "device", node.id))


def credential_response(issued):
    row, token, _ = issued
    return dto.Credential(
        id=row.id, token=token, scope=row.scope, node_id=row.node_id, expires_at=row.expires_at
    )


def revoke(db, actor, credential_id):
    write_lock(db)
    row = db.get(m.Credential, credential_id)
    if row is None:
        raise missing()
    user = db.get(m.User, row.user_id)
    actor.owns(user.id, user.pool_id)
    if row.revoked_at is None:
        row.revoked_at = now(db)
    if row.node_id:
        node = db.get(m.Node, row.node_id)
        session = db.get(m.NodeSession, node.current_session_id) if node.current_session_id else None
        if session and session.credential_id == row.id:
            node.current_session_id = None
            node.capabilities = None
            emit(db, node.pool_id, "node", node.id, "credential_revoked")


def device_node(db, actor):
    if actor.credential.scope != "device":
        raise forbidden()
    node = db.get(m.Node, actor.credential.node_id)
    if node is None or node.pool_id != actor.user.pool_id:
        raise forbidden()
    return node


def session_for(db, actor, session_id, *, touch=False):
    node = device_node(db, actor)
    if not session_id:
        raise Problem("SESSION_REQUIRED", "X-Node-Session is required", 401)
    session = db.get(m.NodeSession, session_id)
    if (
        node.current_session_id != session_id
        or not session
        or session.credential_id != actor.credential.id
    ):
        raise Problem("SESSION_REPLACED", "The device session is no longer current", 409)
    if touch:
        node.last_seen_at = now(db)
    return node, session


def node_state(node, current, offline_seconds):
    if node.mode == "quarantined":
        return node.mode
    if (
        not node.current_session_id
        or not node.last_seen_at
        or node.last_seen_at + timedelta(seconds=offline_seconds) <= current
    ):
        return "offline"
    if node.mode == "paused":
        return "paused"
    if node.mode == "draining":
        return "draining"
    return "ready" if node.capabilities and node.capabilities["runtime"]["healthy"] else "preflight"


def join(db, actor, body, settings):
    write_lock(db)
    node = device_node(db, actor)
    session = m.NodeSession(node_id=node.id, credential_id=actor.credential.id)
    db.add(session)
    db.flush()
    node.current_session_id = session.id
    node.last_seen_at = now(db)
    node.capabilities = payload(body.capabilities) if body.capabilities else None
    state = node_state(node, node.last_seen_at, settings.offline_seconds)
    emit(db, node.pool_id, "node", node.id, "session_opened", state=state)
    return dto.NodeSession(id=session.id, node_id=node.id, state=state)


def set_capabilities(db, actor, session_id: UUID, body, settings):
    write_lock(db)
    node, session = session_for(db, actor, session_id, touch=True)
    previous = node.capabilities
    capabilities = payload(body)
    if previous and (
        body.preflight_revision < previous["preflight_revision"]
        or body.preflight_revision == previous["preflight_revision"]
        and previous != capabilities
    ):
        raise Problem("STALE_PREFLIGHT", "Capabilities must use a new preflight revision", 409)
    node.capabilities = capabilities
    state = node_state(node, node.last_seen_at, settings.offline_seconds)
    if previous != capabilities:
        emit(db, node.pool_id, "node", node.id, "capabilities_updated", state=state)
    return dto.NodeSession(id=session.id, node_id=node.id, state=state)
