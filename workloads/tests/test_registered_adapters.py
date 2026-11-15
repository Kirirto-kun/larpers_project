import json
import subprocess
import sys


def test_adapter_rejects_unregistered_code_without_importing(tmp_path):
    from workloads.adapter import dispatch
    evil = tmp_path / "evil.py"
    marker = tmp_path / "executed"
    evil.write_text(f"open({str(marker)!r}, 'w').write('bad')")
    response = dispatch({"schema_version": "1", "adapter": {"id": str(evil), "version": "1"},
                         "operation": "validate_input", "payload": {}})
    assert response["error"]["code"] == "UNKNOWN_ADAPTER"
    assert response["result"] is None and not marker.exists()


def test_json_protocol_generic_quality_is_unverified_and_command_is_not_run(tmp_path):
    marker = tmp_path / "not-executed"
    request = {"schema_version": "1", "adapter": {"id": "generic.project", "version": "1"},
               "operation": "verify", "payload": {"manifest": {"schema_version": "1", "kind": "generic",
               "command": [sys.executable, "-c", f"open({str(marker)!r},'w').write('bad')"]},
               "outputs": {"files": []}, "policy": {}}}
    result = subprocess.run([sys.executable, "-m", "workloads.adapter"], input=json.dumps(request),
                            text=True, capture_output=True, check=True)
    response = json.loads(result.stdout)
    assert response["schema_version"] == "1" and response["error"] is None
    assert response["result"]["quality"] == "unverified"
    assert not response["result"]["benchmark_eligible"] and not marker.exists()


def test_generic_capture_changed_files_excludes_secrets_and_symlinks(tmp_path):
    from workloads.generic.adapter import snapshot, collect_outputs
    (tmp_path / "source.py").write_text("print('original')")
    (tmp_path / ".env").write_text("TOKEN=secret")
    before = snapshot(tmp_path)
    (tmp_path / "model.pt").write_bytes(b"model")
    (tmp_path / "source.py").write_text("print('changed')")
    output = collect_outputs(tmp_path, before, max_bytes=100)
    assert [row["path"] for row in output["files"]] == ["model.pt", "source.py"]
    assert output["quality"] == "unverified" and not output["benchmark_eligible"]


def test_capture_rejects_symlink_escape_and_oversized_model(tmp_path):
    import pytest
    from workloads.generic.adapter import snapshot, collect_outputs
    root = tmp_path / "project"
    root.mkdir()
    before = snapshot(root)
    (root / "large.pt").write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="quota"):
        collect_outputs(root, before, max_bytes=10)
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    try:
        (root / "escape").symlink_to(outside)
    except OSError:
        pytest.skip("OS does not allow symlink creation for this test account")
    with pytest.raises(ValueError, match="symlink"):
        snapshot(root)


def test_registered_adapter_rejects_user_validator_without_loading_it(tmp_path):
    from workloads.adapter import dispatch
    marker = tmp_path / "executed"
    validator = tmp_path / "validator.py"
    validator.write_text(f"open({str(marker)!r},'w').write('bad')")
    request = {"schema_version": "1", "adapter": {"id": "generic.project", "version": "1"},
               "operation": "validate_input", "payload": {"manifest": {"schema_version": "1", "kind": "generic",
                   "command": ["python", "train.py"], "validator": str(validator)}}}
    response = dispatch(request)
    assert response["error"]["code"] == "INVALID_INPUT" and not marker.exists()


def test_json_protocol_can_read_prepared_output_artifact_paths(tmp_path):
    from workloads.adapter import dispatch
    output = tmp_path / "result.json"
    output.write_text('{"files":[{"path":"model.pt"}]}')
    request = {"schema_version": "1", "adapter": {"id": "generic.project", "version": "1"},
               "operation": "aggregate", "payload": {"manifest": {"schema_version": "1", "kind": "generic",
                   "command": ["python", "train.py"]}, "output_paths": [str(output)]}}
    response = dispatch(request)
    assert response["error"] is None
    assert response["result"]["outputs"][0]["files"][0]["path"] == "model.pt"
