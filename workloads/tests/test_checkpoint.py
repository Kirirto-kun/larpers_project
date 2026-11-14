import json
import pytest
from campus.checkpoint import save_checkpoint, load_checkpoint, publish_checkpoint


def test_only_complete_compatible_checkpoints_can_resume(tmp_path):
    binding = dict(code_digest="a" * 64, input_digest="b" * 64,
                   environment_digest="c" * 64, trainer_digest="d" * 64)
    result = save_checkpoint(tmp_path, "step-1", {"state.json": b'{"step":1}'}, binding)
    assert load_checkpoint(result, binding)["state.json"] == b'{"step":1}'
    with pytest.raises(ValueError, match="compatible"):
        load_checkpoint(result, {**binding, "code_digest": "e" * 64})
    (result / "state.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="digest"):
        load_checkpoint(result, binding)


def test_interrupted_checkpoint_never_replaces_pointer(tmp_path):
    binding = {k: "a" * 64 for k in ("code_digest", "input_digest", "environment_digest", "trainer_digest")}
    complete = save_checkpoint(tmp_path, "step-1", {"state.json": b"{}"}, binding)
    class Transport:
        def __init__(self): self.calls = []
        def upload(self, path):
            self.calls.append(("upload", path.name))
            return {"artifact_id": path.name, "digest": "f" * 64}
        def publish(self, manifest):
            self.calls.append(("publish", manifest))
            return {"checkpoint_id": "accepted"}
    transport = Transport()
    assert publish_checkpoint(complete, binding, transport)["checkpoint_id"] == "accepted"
    assert transport.calls[-1][0] == "publish"
    (complete / "READY").unlink()
    transport.calls.clear()
    with pytest.raises(ValueError, match="complete"):
        publish_checkpoint(complete, binding, transport)
    assert transport.calls == []


def test_checkpoint_rejects_path_escape_and_existing_snapshot(tmp_path):
    binding = {k: "a" * 64 for k in ("code_digest", "input_digest", "environment_digest", "trainer_digest")}
    with pytest.raises(ValueError):
        save_checkpoint(tmp_path, "../escape", {"x": b"x"}, binding)
    with pytest.raises(ValueError):
        save_checkpoint(tmp_path, "safe", {"../escape": b"x"}, binding)
    result = save_checkpoint(tmp_path, "safe", {"x": b"x"}, binding)
    with pytest.raises(FileExistsError):
        save_checkpoint(tmp_path, "safe", {"x": b"different"}, binding)


def test_publisher_uploads_verified_snapshot_even_when_original_changes(tmp_path):
    binding = {k: "a" * 64 for k in ("code_digest", "input_digest", "environment_digest", "trainer_digest")}
    root = save_checkpoint(tmp_path, "step", {"a": b"first", "b": b"second"}, binding)
    class Transport:
        def __init__(self): self.payloads = {}
        def upload(self, path):
            self.payloads[path.name] = path.read_bytes()
            if path.name == "a":
                (root / "b").write_bytes(b"modified after validation")
            return {"artifact_id": path.name}
        def publish(self, manifest): return manifest
    transport = Transport()
    publish_checkpoint(root, binding, transport)
    assert transport.payloads["b"] == b"second"
