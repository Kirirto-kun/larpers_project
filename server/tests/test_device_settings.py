from uuid import UUID

from test_accounts import register

from campus import models as m


def test_members_can_only_read_own_nodes_and_stats(cp):
    _, session = cp.device()
    own_job = cp.job()
    identity = register(cp)
    assert cp.call("GET", "/nodes", headers={}) == []
    cp.call("GET", f"/nodes/{session['node_id']}", headers={}, code=404)
    browser = {"Origin": cp.runtime.settings.public_origin, "X-CSRF-Token": identity["csrf_token"]}
    cp.call(
        "PATCH", f"/nodes/{session['node_id']}/settings", headers=browser, body={"mode": "max"}, code=404
    )
    cp.call(
        "POST",
        f"/nodes/{session['node_id']}/control",
        headers=browser,
        body={"action": "resume"},
        code=404,
    )
    cp.call("GET", f"/jobs/{own_job['id']}", headers={}, code=404)
    stats = cp.call("GET", "/me/stats", headers={})
    assert stats["devices_total"] == stats["devices_online"] == stats["accepted_units"] == 0
    assert stats["jobs_by_state"] == {} and stats["history"] == []
    assert stats["recent_results"] == [] and stats["contributed_seconds"] is None
    assert identity["id"] != cp.call("GET", "/auth/me")["id"]


def test_settings_poll_never_fabricates_worker_heartbeat_and_ack_is_revision_fenced(cp):
    device, session = cp.device()
    bearer = {"Authorization": device["Authorization"]}
    node_id = session["node_id"]
    before = cp.call("GET", f"/nodes/{node_id}")
    settings = cp.call("GET", "/device/settings", headers=bearer)
    assert settings == {"revision": 1, "mode": "medium", "desired_state": "running"}
    node = cp.call("GET", f"/nodes/{node_id}")
    assert node["last_seen_at"] == before["last_seen_at"]
    assert node["agent_contact_at"] and not node["supports_web_controls"]
    updated = cp.call("PATCH", f"/nodes/{node_id}/settings", body={"mode": "low"})
    assert updated["settings"]["revision"] == 2
    duplicate = cp.call("PATCH", f"/nodes/{node_id}/settings", body={"mode": "low"})
    assert duplicate["settings"]["revision"] == 2
    ack = {"revision": 1, "mode": "medium", "actual_state": "running"}
    cp.call("POST", "/device/settings/ack", headers=bearer, body=ack, code=409)
    cp.call("POST", "/device/settings/ack", headers=bearer, body={**ack, "revision": 3}, code=409)
    cp.call(
        "POST",
        "/device/settings/ack",
        headers=bearer,
        body={"revision": 2, "mode": "low", "actual_state": "running"},
    )
    node = cp.call("GET", f"/nodes/{node_id}")
    assert node["supports_web_controls"] and node["applied_revision"] == 2
    cp.call("GET", "/device/settings", code=403)


def test_pause_session_fence_offline_truth_and_quarantine_cannot_be_bypassed(cp):
    device, session = cp.device()
    bearer = {"Authorization": device["Authorization"]}
    path = f"/nodes/{session['node_id']}"
    paused = cp.call("PATCH", path + "/settings", body={"desired_state": "paused"})
    assert paused["settings"]["desired_state"] == "paused"
    cp.claim(device, code=409)
    resumed = cp.call("PATCH", path + "/settings", body={"desired_state": "running"})
    assert resumed["state"] == "offline"
    assert resumed["last_seen_at"] == paused["last_seen_at"]
    quarantined = cp.call("POST", path + "/control", body={"action": "quarantine"})
    assert quarantined["state"] == "quarantined"
    cp.call("PATCH", path + "/settings", body={"desired_state": "running"}, code=409)
    settings = cp.call("GET", "/device/settings", headers=bearer)
    assert settings["desired_state"] == "paused"
    cp.call(
        "POST",
        "/device/settings/ack",
        headers=bearer,
        body={"revision": settings["revision"], "mode": settings["mode"], "actual_state": "running"},
        code=409,
    )
    with cp.runtime.sessions.begin() as db:
        node = db.get(m.Node, UUID(session["node_id"]))
        assert node.current_session_id is None


def test_stats_use_real_owned_contribution_and_job_history(cp):
    device, _ = cp.device()
    job = cp.job()
    assignment = cp.claim(device)
    cp.complete(device, assignment)
    stats = cp.call("GET", "/me/stats")
    assert stats["devices_total"] == stats["devices_online"] == 1
    assert stats["accepted_units"] > 0
    assert stats["history"] and stats["history"][-1]["accepted_units"] == stats["accepted_units"]
    assert sum(stats["jobs_by_state"].values()) == 1
    assert stats["recent_results"][0]["id"] == job["id"]


def test_drain_completion_ack_can_truthfully_report_paused(cp):
    device, session = cp.device()
    node = cp.call("PATCH", f"/nodes/{session['node_id']}/settings", body={"desired_state": "draining"})
    cp.call(
        "POST",
        "/device/settings/ack",
        headers={"Authorization": device["Authorization"]},
        body={"revision": node["settings"]["revision"], "mode": "medium", "actual_state": "paused"},
    )
    result = cp.call("GET", f"/nodes/{session['node_id']}")
    assert result["actual_state"] == "paused" and result["settings"]["desired_state"] == "draining"


def test_member_can_list_and_control_owned_device_with_csrf(cp):
    identity = register(cp)
    origin = {"Origin": cp.runtime.settings.public_origin, "X-CSRF-Token": identity["csrf_token"]}
    credential = cp.call("POST", "/auth/cli-credentials", headers=origin, code=201)
    owner = cp.owner
    cp.owner = {"Authorization": "Bearer " + credential["token"]}
    try:
        device, session = cp.device()
    finally:
        cp.owner = owner
    node_id = session["node_id"]
    nodes = cp.call("GET", "/nodes", headers={})
    assert len(nodes) == 1 and nodes[0]["owner_id"] == identity["id"]
    cp.call("PATCH", f"/nodes/{node_id}/settings", headers={}, body={"mode": "low"}, code=403)
    node = cp.call("PATCH", f"/nodes/{node_id}/settings", headers=origin, body={"mode": "low"})
    assert node["settings"]["mode"] == "low"
    cp.call("POST", f"/nodes/{node_id}/control", headers=origin, body={"action": "quarantine"}, code=403)
    cp.call("DELETE", f"/credentials/{credential['id']}", headers=origin, code=204)
    assert (
        cp.call("GET", "/device/settings", headers={"Authorization": device["Authorization"]})["mode"]
        == "low"
    )
