from uuid import UUID, uuid4

from sqlalchemy import select

from campus import auth
from campus import models as m


def test_scrypt_passwords_are_salted_bounded_and_fail_closed():
    assert hasattr(auth, "hash_password"), "account password hashing is missing"
    first = auth.hash_password("a long private password")
    second = auth.hash_password("a long private password")
    assert first != second
    assert "a long private password" not in first
    assert auth.verify_password("a long private password", first)
    assert not auth.verify_password("wrong password", first)
    for malformed in (None, "", "scrypt$999999999$8$1$bad$bad", first + "$extra"):
        assert not auth.verify_password("a long private password", malformed)


def register(cp, email=None):
    me = cp.call("GET", "/auth/me")
    cp.runtime.settings.registration_pool_id = UUID(me["pool_id"])
    return cp.call(
        "POST",
        "/auth/register",
        headers={"Origin": cp.runtime.settings.public_origin},
        body={
            "name": "  New Member  ",
            "email": email or f"{uuid4()}@example.com",
            "password": "a long private password",
        },
        code=201,
    )


def test_registration_normalization_password_login_and_member_boundaries(cp):
    email = f"{uuid4()}@example.com"
    identity = register(cp, "  " + email.upper() + "  ")
    assert identity["email"] == email
    assert identity["name"] == "New Member"
    assert identity["role"] == "member"
    origin = {"Origin": cp.runtime.settings.public_origin}
    cp.call(
        "POST",
        "/invitations",
        headers={**origin, "X-CSRF-Token": identity["csrf_token"]},
        body={"kind": "member", "name": "Admin"},
        code=403,
    )
    cp.call("GET", "/pool/summary", headers={}, code=403)
    cp.call("GET", "/admin/users", headers={}, code=403)
    cp.call(
        "POST",
        "/auth/register",
        headers=origin,
        body={"name": "Other", "email": email, "password": "another long password"},
        code=409,
    )
    cp.call("POST", "/auth/logout", headers={**origin, "X-CSRF-Token": identity["csrf_token"]}, code=204)
    logged = cp.call(
        "POST",
        "/auth/login",
        headers=origin,
        body={"email": email.upper(), "password": "a long private password"},
    )
    assert logged["id"] == identity["id"]
    assert cp.call("GET", "/auth/me", headers={})["email"] == email
    with cp.runtime.sessions.begin() as db:
        user = db.get(m.User, UUID(identity["id"]))
        assert user.password_hash and user.password_hash != "a long private password"


def test_entry_origin_csrf_generic_login_and_input_bounds(cp):
    body = {"name": "Member", "email": f"{uuid4()}@example.com", "password": "long enough password"}
    cp.call("POST", "/auth/register", body=body, headers={}, code=403)
    cp.call("POST", "/auth/register", body=body, headers={"Origin": "https://evil.test"}, code=403)
    origin = {"Origin": cp.runtime.settings.public_origin}
    cp.call("POST", "/auth/register", body={**body, "password": "short"}, headers=origin, code=422)
    first = cp.call(
        "POST",
        "/auth/login",
        headers=origin,
        body={"email": body["email"], "password": "wrong password"},
        code=401,
    )
    identity = register(cp, body["email"])
    second = cp.call(
        "POST",
        "/auth/login",
        headers=origin,
        body={"email": body["email"], "password": "wrong password"},
        code=401,
    )
    assert (first["code"], first["message"]) == (second["code"], second["message"])
    cp.call("POST", "/auth/account", headers=origin, body=body, code=403)
    cp.call(
        "POST",
        "/auth/account",
        headers={**origin, "X-CSRF-Token": identity["csrf_token"]},
        body=body,
        code=409,
    )


def test_existing_owner_activation_preserves_identity_and_one_time_invite(cp):
    with cp.runtime.sessions.begin() as db:
        actor = db.scalar(
            select(m.User)
            .join(m.Credential)
            .where(m.Credential.token_hash == auth.secret_hash(cp.owner["Authorization"].split()[1]))
        )
        old_id = actor.id
        invite = auth.create_invite(
            db, actor, auth.dto.InviteCreate(kind="member", name=actor.name), existing_user=old_id
        )
    origin = {"Origin": cp.runtime.settings.public_origin}
    login = cp.call("POST", "/auth/login", headers=origin, body={"token": invite.token})
    body = {"name": "Owner", "email": f"{uuid4()}@example.com", "password": "private owner password"}
    identity = cp.call(
        "POST", "/auth/account", headers={**origin, "X-CSRF-Token": login["csrf_token"]}, body=body
    )
    assert identity["id"] == str(old_id)
    assert identity["role"] == "owner"
    cp.call("POST", "/auth/login", headers=origin, body={"token": invite.token}, code=401)
    assert cp.call("GET", "/auth/me")["id"] == str(old_id)


def test_public_admin_email_never_grants_admin(cp):
    identity = register(cp, "jafarman2007@gmail.com")
    assert identity["role"] == "member"


def test_registration_rate_limit_and_pool_configuration(cp):
    with cp.runtime.sessions.begin() as db:
        db.add(m.Pool(name="Not a registration destination"))
    body = {"name": "Member", "email": f"{uuid4()}@example.com", "password": "long enough password"}
    cp.call(
        "POST",
        "/auth/register",
        headers={"Origin": cp.runtime.settings.public_origin},
        body=body,
        code=503,
    )
    statuses = [cp.client.post("/api/v1/auth/register", json=body).status_code for _ in range(35)]
    assert 429 in statuses
    assert set(statuses) <= {403, 429}


def test_reserved_owner_email_requires_authenticated_matching_one_time_activation(cp):
    owner = cp.call("GET", "/auth/me")
    cp.runtime.settings.registration_pool_id = UUID(owner["pool_id"])
    email = f"reserved-{uuid4()}@example.com"
    with cp.runtime.sessions.begin() as db:
        user = db.get(m.User, UUID(owner["id"]))
        user.email = email
        assert user.password_hash is None
        invite = auth.create_invite(
            db, user, auth.dto.InviteCreate(kind="member", name=user.name), existing_user=user.id
        )
    origin = {"Origin": cp.runtime.settings.public_origin}
    body = {"name": "Activated owner", "email": email, "password": "private owner password"}
    assert (
        cp.call(
            "POST",
            "/auth/login",
            headers=origin,
            body={"email": email, "password": body["password"]},
            code=401,
        )["code"]
        == "UNAUTHORIZED"
    )
    assert (
        cp.call("POST", "/auth/register", headers=origin, body=body, code=409)["code"] == "EMAIL_IN_USE"
    )
    login = cp.call("POST", "/auth/login", headers=origin, body={"token": invite.token})
    assert login["id"] == owner["id"] and login["email"] == email
    browser = {**origin, "X-CSRF-Token": login["csrf_token"]}
    cp.call(
        "POST",
        "/auth/account",
        headers=browser,
        body={**body, "email": f"other-{uuid4()}@example.com"},
        code=409,
    )
    identity = cp.call(
        "POST", "/auth/account", headers=browser, body={**body, "email": "  " + email.upper() + "  "}
    )
    assert identity["id"] == owner["id"] and identity["role"] == "owner"
    assert identity["email"] == email
    cp.call("POST", "/auth/account", headers=browser, body=body, code=409)
    cp.call("POST", "/auth/login", headers=origin, body={"token": invite.token}, code=401)
    logged = cp.call(
        "POST", "/auth/login", headers=origin, body={"email": email, "password": body["password"]}
    )
    assert logged["id"] == owner["id"] and logged["role"] == "owner"
