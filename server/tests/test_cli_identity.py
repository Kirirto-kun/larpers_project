from uuid import UUID, uuid4

from sqlalchemy import select

from campus import auth
from campus import models as m


def account(cp):
    owner = cp.call("GET", "/auth/me")
    cp.runtime.settings.registration_pool_id = UUID(owner["pool_id"])
    body = {
        "name": "CLI user",
        "email": f"cli-{uuid4()}@example.com",
        "password": "private test password",
    }
    identity = cp.call(
        "POST",
        "/auth/register",
        body=body,
        headers={"Origin": cp.runtime.settings.public_origin},
        code=201,
    )
    return body, identity


def test_cli_login_issues_expiring_member_credential_and_local_logout_revokes_only_it(cp):
    body, identity = account(cp)
    result = cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={"email": " " + body["email"].upper() + " ", "password": body["password"]},
    )
    assert result["user_id"] == identity["id"]
    assert result["pool_id"] == identity["pool_id"] and result["role"] == "member"
    assert len(result["token"]) >= 32 and result["expires_at"]
    assert "password" not in result
    headers = {"Authorization": "Bearer " + result["token"]}
    assert cp.call("GET", "/auth/me", headers=headers)["id"] == identity["id"]
    cp.call("GET", "/admin/users", headers=headers, code=403)
    with cp.runtime.sessions() as db:
        row = db.scalar(
            select(m.Credential).where(m.Credential.token_hash == auth.secret_hash(result["token"]))
        )
        assert row.scope == "cli" and row.csrf_hash is None
    cp.call("POST", "/auth/cli-logout", headers=headers, code=204)
    cp.call("GET", "/auth/me", headers=headers, code=401)
    # The separately issued browser session remains usable.
    assert cp.call("GET", "/auth/me", headers={})["id"] == identity["id"]


def test_cli_login_is_generic_bounded_and_rejects_cross_origin_and_non_cli_logout(cp):
    body, _ = account(cp)
    wrong = cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={**body | {"name": None}, "email": body["email"], "password": "wrong"},
        code=422,
    )
    assert wrong["code"] == "INVALID_REQUEST"
    first = cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={"email": body["email"], "password": "wrong"},
        code=401,
    )
    second = cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={"email": f"missing-{uuid4()}@example.com", "password": "wrong"},
        code=401,
    )
    assert (first["code"], first["message"]) == (second["code"], second["message"])
    cp.call(
        "POST",
        "/auth/cli-login",
        headers={"Origin": "https://evil.test"},
        body={"email": body["email"], "password": body["password"]},
        code=403,
    )
    cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={"email": body["email"], "password": "x" * 129},
        code=422,
    )
    device, _ = cp.device()
    cp.call("POST", "/auth/cli-logout", headers=device, code=403)


def test_cli_login_entry_rate_limit(cp):
    statuses = [
        cp.client.post(
            "/api/v1/auth/cli-login", json={"email": "missing@example.com", "password": "wrong"}
        ).status_code
        for _ in range(35)
    ]
    assert 429 in statuses and set(statuses) <= {401, 429}


def test_artifact_lookup_reuses_only_owned_ready_input_bytes_and_kind(cp):
    artifact = cp.upload(b"unchanged dataset", "dataset.csv", part_size=4096)
    route = (
        f"/artifacts/lookup?sha256={artifact['sha256']}&size_bytes={artifact['size_bytes']}&kind=input"
    )
    assert cp.call("GET", route)["id"] == artifact["id"]
    cp.call("GET", route.replace("kind=input", "kind=project"), code=404)
    cp.call("GET", route.replace(f"size_bytes={artifact['size_bytes']}", "size_bytes=999"), code=404)
    cp.call("GET", "/artifacts/lookup?sha256=bad&size_bytes=1&kind=input", code=422)
    cp.call("GET", route.replace("kind=input", "kind=output"), code=422)
    device, _ = cp.device()
    cp.call("GET", route, headers=device, code=403)
    body, _ = account(cp)
    cli = cp.call(
        "POST",
        "/auth/cli-login",
        headers={},
        body={"email": body["email"], "password": body["password"]},
    )
    cp.call("GET", route, headers={"Authorization": "Bearer " + cli["token"]}, code=404)
    # A missing blob must be uploaded again instead of silently reusing its row.
    cp.runtime.storage.blob(artifact["sha256"]).unlink()
    cp.call("GET", route, code=404)
