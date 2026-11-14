import pytest
from environments.guard.guard import LeaseWatch
from environments.guard.files import extract_project, scan, capture
import zipfile
import io


def test_guard_uses_monotonic_deadlines_and_rejects_replay():
    watch = LeaseWatch(10_000, now=0, challenge="a" * 32)
    grant = {"sequence": 1, "challenge": "a" * 32, "remaining_ms": 2000}
    assert watch.accept(grant, now=0.2)
    assert not watch.expired(1.9)
    assert watch.expired(2.01)  # challenge age deducted; stale files cannot extend life
    assert not watch.accept(grant, now=2.1)


def test_guard_always_keeps_hard_deadline_and_challenge_age():
    watch = LeaseWatch(3000, now=0, challenge="b" * 32)
    assert not watch.accept({"sequence": 100, "challenge": "wrong", "remaining_ms": 10000}, now=.1)
    assert watch.accept({"sequence": 1, "challenge": "b" * 32, "remaining_ms": 99999}, now=1)
    assert watch.expired(3.01)
    assert not watch.accept({"sequence": 2, "challenge": watch.challenge, "remaining_ms": 99999}, now=3.1)


def test_normal_ten_second_renewals_survive_multiple_lease_windows():
    watch=LeaseWatch(300_000,now=0,challenge="c"*32)
    assert watch.accept({"sequence":1,"challenge":watch.challenge,"remaining_ms":60_000},now=0)
    old={"sequence":2,"challenge":watch.challenge,"remaining_ms":60_000}
    for sequence in range(2,10):
        now=(sequence-1)*10
        assert watch.refresh_challenge(now=now-.2)
        assert watch.accept({"sequence":sequence,"challenge":watch.challenge,"remaining_ms":60_000},now=now)
        assert not watch.accept(old,now=now+.1)
    assert not watch.expired(100)
    assert watch.expired(141)
    assert watch.expired(301)


def test_project_archive_rejects_escape_and_zip_bomb(tmp_path):
    for name in ["../outside", "C:/escape", "a\\..\\b", "/escape"]:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr(name, "bad")
        p = tmp_path / "project.zip"
        p.write_bytes(data.getvalue())
        with pytest.raises(ValueError):
            extract_project(p, tmp_path / "workspace", max_bytes=100)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("huge", "0" * 1000)
    p.write_bytes(data.getvalue())
    with pytest.raises(ValueError, match="quota"):
        extract_project(p, tmp_path / "workspace", max_bytes=100)


def test_output_capture_preserves_model_and_excludes_secrets(tmp_path):
    workspace = tmp_path / "workspace"
    outputs = tmp_path / "outputs"
    workspace.mkdir(); outputs.mkdir()
    (workspace / "train.py").write_text("source")
    before = scan(workspace, 100, 10000)
    (workspace / "model.pt").write_bytes(b"weights")
    (workspace / ".env").write_text("TOKEN=secret")
    (workspace / ".campus-env").mkdir()
    (workspace / ".campus-env" / "installed-package.py").write_text("prepared dependency")
    result = capture(workspace, outputs, tmp_path / "export", before, [], max_bytes=1000)
    assert [f["path"] for f in result["files"]] == ["workspace/model.pt"]
    assert (tmp_path / "export" / "workspace" / "model.pt").read_bytes() == b"weights"


def test_output_capture_honors_project_ignore_and_refuses_missing_output(tmp_path):
    workspace=tmp_path / "workspace"; workspace.mkdir()
    outputs=tmp_path / "outputs"; outputs.mkdir()
    (workspace / ".campusignore").write_text("cache/\n*.tmp\n")
    before=scan(workspace,100,10000)
    (workspace / "cache").mkdir(); (workspace / "cache" / "huge.txt").write_text("ignored")
    (workspace / "debug.tmp").write_text("ignored")
    (workspace / "model.pt").write_bytes(b"weights")
    result=capture(workspace,outputs,tmp_path/"export",before,[],max_bytes=1000)
    assert [e["path"] for e in result["files"]]==["workspace/model.pt"]
    with pytest.raises(ValueError,match="missing"):
        capture(workspace,outputs,tmp_path/"another",before,["missing.pt"],max_bytes=1000)


def test_child_environment_has_deterministic_cuda_without_host_credentials(monkeypatch):
    from environments.guard.guard import workload_environment
    monkeypatch.setenv("HF_TOKEN","secret")
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG","unsafe")
    environment=workload_environment({"threads":2})
    assert environment["CUBLAS_WORKSPACE_CONFIG"]==":4096:8"
    assert environment["HF_HUB_OFFLINE"]=="1"
    assert "HF_TOKEN" not in environment
    assert environment["OMP_NUM_THREADS"]=="2"
