"""Desired controls are durable; polling never claims worker liveness."""

from . import dto
from .auth import device_node
from .db import now, write_lock
from .errors import Problem
from .events import emit


def describe(node):
    return dto.DeviceSettings(
        revision=node.settings_revision, mode=node.contribution_mode, desired_state=node.desired_state
    )


def read(db, actor):
    node = device_node(db, actor)
    node.agent_contact_at = now(db)
    return describe(node)


def update(db, node, body):
    write_lock(db)
    if node.mode == "quarantined":
        raise Problem("QUARANTINED", "An administrator must release the device", 409)
    mode = body.mode or node.contribution_mode
    state = body.desired_state or node.desired_state
    if (mode, state) == (node.contribution_mode, node.desired_state):
        return node
    node.contribution_mode = mode
    node.desired_state = state
    node.settings_revision += 1
    node.settings_error = None
    node.mode = {"running": "ready", "paused": "paused", "draining": "draining"}[state]
    if state == "paused":
        node.current_session_id = None
    emit(db, node.pool_id, "node", node.id, "settings_updated", revision=node.settings_revision)
    return node


def acknowledge(db, actor, body):
    write_lock(db)
    node = device_node(db, actor)
    if body.revision != node.settings_revision:
        raise Problem("STALE_SETTINGS", "Fetch the current device settings before acknowledging", 409)
    if body.mode != node.contribution_mode:
        raise Problem("SETTINGS_MISMATCH", "Acknowledged mode differs from desired settings", 409)
    if node.mode == "quarantined" and body.actual_state != "paused":
        raise Problem("QUARANTINED", "An administrator must release the device", 409)
    completed_drain = node.desired_state == "draining" and body.actual_state == "paused"
    if not body.error and body.actual_state != node.desired_state and not completed_drain:
        raise Problem("SETTINGS_MISMATCH", "Acknowledged state differs from desired settings", 409)
    node.applied_revision = body.revision
    node.actual_state = body.actual_state
    node.applied_mode = body.mode
    node.settings_error = body.error
    node.agent_contact_at = now(db)
    return describe(node)
