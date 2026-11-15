from uuid import uuid4


def test_single_use_and_csrf(cp):
    invite = cp.call("POST", "/invitations", body={"kind": "member", "name": "Member"}, code=201)
    origin = cp.runtime.settings.public_origin
    csrf = cp.call("POST", "/auth/login", body={"token": invite["token"]}, headers={"Origin": origin})[
        "csrf_token"
    ]
    cp.call("POST", "/auth/login", body={"token": invite["token"]}, headers={"Origin": origin}, code=401)
    assert cp.call("GET", "/auth/me", headers={})["role"] == "member"
    invite_body = {"kind": "device", "name": "Laptop"}
    cp.call("POST", "/invitations", body=invite_body, headers={"Origin": origin}, code=403)
    cp.call(
        "POST",
        "/invitations",
        body=invite_body,
        headers={"Origin": origin, "X-CSRF-Token": csrf},
        code=201,
    )
    cp.call("POST", "/auth/logout", headers={"Origin": origin, "X-CSRF-Token": csrf}, code=204)
    cp.call("GET", "/auth/me", headers={}, code=401)


def test_device_cannot_login_as_user_or_read_jobs(cp):
    invite = cp.call("POST", "/invitations", body={"kind": "device", "name": "Laptop"}, code=201)
    cp.call(
        "POST",
        "/auth/login",
        body={"token": invite["token"]},
        headers={"Origin": cp.runtime.settings.public_origin},
        code=401,
    )
    node, _ = cp.device()
    cp.call("GET", "/jobs", headers=node, code=403)


def test_job_idempotency(cp):
    view = cp.preview()
    headers = {**cp.owner, "Idempotency-Key": str(uuid4())}
    body = {"preview_id": view["id"], "plan_revision": 1}
    first = cp.call("POST", "/jobs", headers=headers, body=body, code=201)
    second = cp.call("POST", "/jobs", headers=headers, body=body, code=201)
    assert first["id"] == second["id"]
    cp.call("POST", "/jobs", headers=headers, body={**body, "plan_revision": 2}, code=409)
