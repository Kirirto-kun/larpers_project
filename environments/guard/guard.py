"""PID1 supervisor: root owns lease state, workload runs as UID/GID 65532.

The supervisor has SETUID, SETGID, KILL, CHOWN and DAC_OVERRIDE. Changing the
workload UID clears its permitted/effective capabilities. No workload-controlled signal,
file, environment variable or wall clock can extend its hard runtime deadline.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

from campus.checkpoint import canonical, load_checkpoint, save_checkpoint
from environments.guard.files import capture, extract_project, scan, read_regular


class LeaseWatch:
    def __init__(self, hard_runtime_ms, *, now, challenge=None):
        if not 0 < hard_runtime_ms <= 7 * 24 * 3600 * 1000:
            raise ValueError("invalid hard runtime")
        self.hard = now + hard_runtime_ms / 1000
        self.deadline = now
        self.sequence = 0
        self.challenge = challenge or secrets.token_hex(32)
        self.challenge_at = now

    def accept(self, grant, *, now):
        sequence = grant.get("sequence")
        remaining = grant.get("remaining_ms")
        if (type(sequence) is not int or sequence <= self.sequence
                or type(remaining) is not int or remaining <= 0
                or grant.get("challenge") != self.challenge or now >= self.hard
                or now - self.challenge_at > 5):
            return False
        deadline = min(self.hard, now + remaining / 1000 - (now - self.challenge_at))
        if deadline <= now:
            return False
        self.sequence = sequence
        self.deadline = deadline
        self.challenge = secrets.token_hex(32)
        self.challenge_at = now
        return True

    def expired(self, now):
        return now >= min(self.deadline, self.hard)

    def refresh_challenge(self, *, now):
        if now-self.challenge_at < 1 or now>=self.hard:
            return False
        self.challenge=secrets.token_hex(32)
        self.challenge_at=now
        return True


def prepare_shared_mounts(state, export):
    """Keep host-private bind ownership; choose a different unprivileged UID."""
    owners={}
    for directory in (Path(state),Path(export)):
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeError("supervisor bind must be a real private directory")
        info=directory.stat()
        if info.st_uid==os.getuid():
            directory.chmod(0o700)
            info=directory.stat()
        if info.st_mode & 0o077:
            raise RuntimeError("bind filesystem cannot protect supervisor state; use private Linux filesystem storage")
        owners[directory]=(info.st_uid,info.st_gid)
    workload_uid=65532
    while workload_uid in {0,*[owner[0] for owner in owners.values()]}:
        workload_uid-=1
    return workload_uid,owners


def handoff_tree(root, owner):
    """Publish files to the trusted host owner without granting workload access."""
    root=Path(root)
    if not root.exists(): return
    for parent,dirs,files in os.walk(root,followlinks=False):
        for path in [Path(parent),*[Path(parent)/name for name in dirs+files]]:
            info=path.lstat()
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError("special file in protected export")
            desired=0o700 if path.is_dir() else 0o600
            if info.st_uid!=owner[0] or info.st_gid!=owner[1] or info.st_mode & 0o777!=desired:
                # CHOWN lets PID1 normalize mode without granting CAP_FOWNER.
                os.chown(path,0,0,follow_symlinks=False)
                path.chmod(desired)
                os.chown(path,*owner,follow_symlinks=False)


def atomic_json(path, value, *, owner=None):
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        tmp.chmod(0o600)
        f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
        if owner is not None:
            os.fchown(f.fileno(),*owner)
    os.replace(tmp, path)


def _json_file(path):
    # Check the opened descriptor, not pathname metadata from before open. A
    # workload must never block PID1 by substituting a FIFO or symlink.
    return json.loads(read_regular(path.parent,path.name,1<<20))


def _drop_identity(uid=65532):
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "cannot set no_new_privs")


def _kill_children(sig):
    # PID1 in this private PID namespace can stop even descendants which setsid().
    # Refuse to run on a host: kill(-1) must never target host processes.
    if os.getpid() != 1:
        raise RuntimeError("guard must run as container PID1")
    try:
        os.kill(-1, sig)
    except ProcessLookupError:
        pass


def export_ready_checkpoints(checkpoints, export, exported, config, *, owner=None):
    for snapshot in Path(checkpoints).iterdir():
        if snapshot.name.startswith(".") or snapshot.name in exported or not snapshot.is_dir() or snapshot.is_symlink() or not (snapshot / "READY").is_file():
            continue
        binding = _json_file(snapshot / "manifest.json")["binding"]
        files = load_checkpoint(snapshot, binding, max_bytes=config["checkpoint_bytes"])
        already = scan(Path(export) / "checkpoints", config["max_files"], config["checkpoint_bytes"], exclude_secrets=False)
        if sum(e["size_bytes"] for e in already.values()) + sum(map(len, files.values())) + 65536 > config["checkpoint_bytes"]:
            raise ValueError("pending checkpoint export quota exceeded")
        save_checkpoint(Path(export) / "checkpoints", snapshot.name, files, binding, max_bytes=config["checkpoint_bytes"])
        if owner is not None:
            handoff_tree(Path(export)/"checkpoints",owner)
        exported.add(snapshot.name)


def workload_environment(config):
    return {"PATH": "/opt/campus/.venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
            "PYTHONPATH": "/opt/campus:/opt/campus/sdk/python", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false",
            "CUBLAS_WORKSPACE_CONFIG": ":4096:8", "OMP_NUM_THREADS": str(config.get("threads", 1)),
            "CAMPUS_OUTPUT_DIR": "/outputs", "CAMPUS_CHECKPOINT_DIR": "/checkpoints"}


def main():
    if os.name != "posix" or os.getpid() != 1 or os.getuid() != 0:
        raise RuntimeError("guard requires Linux container PID1 with supervisor UID0")
    control, state, export = Path("/run/campus-lease"), Path("/run/campus-state"), Path("/export")
    workload_uid,owners=prepare_shared_mounts(state,export)
    config = _json_file(control / "config.json")
    now = time.monotonic()
    watch = LeaseWatch(config["hard_runtime_ms"], now=now)
    workspace, outputs, checkpoints = Path("/workspace"), Path("/outputs"), Path("/checkpoints")
    for directory in (workspace, outputs, checkpoints, Path('/tmp')):
        # Normalize mode while PID1 owns the mount; after UID handoff it has
        # deliberately no CAP_FOWNER. /tmp must follow a dynamic workload UID too.
        os.chown(directory,0,0)
        directory.chmod(0o700)
        os.chown(directory, workload_uid, workload_uid)
    if Path("/inputs/project.zip").is_file():
        extract_project("/inputs/project.zip", workspace, max_bytes=config["workspace_bytes"])
        for parent, dirs, files in os.walk(workspace):
            os.chown(parent, workload_uid, workload_uid)
            for name in dirs + files:
                os.chown(Path(parent) / name, workload_uid, workload_uid)
    before = scan(workspace, config["max_files"], config["workspace_bytes"])
    watch.challenge_at = time.monotonic()
    atomic_json(state / "challenge.json", {"challenge": watch.challenge, "accepted_sequence": 0},owner=owners[state])
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    start_deadline = min(watch.hard, time.monotonic() + 5)

    def update():
        try:
            accepted = watch.accept(_json_file(control / "lease.json"), now=time.monotonic())
        except (FileNotFoundError, ValueError, KeyError, OSError):
            accepted = False
        refreshed=watch.refresh_challenge(now=time.monotonic())
        if accepted or refreshed:
            atomic_json(state / "challenge.json", {"challenge": watch.challenge, "accepted_sequence": watch.sequence},owner=owners[state])
        return accepted

    while watch.sequence == 0:
        update()
        if stopped or time.monotonic() >= start_deadline:
            return 124
        if watch.sequence == 0:
            time.sleep(.05)
    environment = workload_environment(config)
    if Path("/resume/READY").is_file():
        binding = config.get("resume_binding")
        load_checkpoint("/resume", binding, max_bytes=config["checkpoint_bytes"])
        environment["CAMPUS_RESUME_FROM"] = "/resume"
    launcher="import sys;sys.path[:0]=['/opt/campus','/opt/campus/sdk/python'];from environments.offline import prepare_and_exec;prepare_and_exec(sys.argv[1:])"
    command=['/opt/campus/.venv/bin/python','-I','-c',launcher,*config['argv']]
    child = subprocess.Popen(command, cwd=config["working_dir"], env=environment,
                             preexec_fn=lambda:_drop_identity(workload_uid), start_new_session=True)
    exported = set()
    checkpoint_error = None
    reason = None
    next_check = time.monotonic()
    while child.poll() is None:
        update()
        current = time.monotonic()
        if stopped or watch.expired(current):
            reason = "stopped" if stopped else "lease_or_runtime_expired"
            break
        if current >= next_check:
            next_check = current + 1
            try:
                scan(workspace, config["max_files"], config["workspace_bytes"], exclude_secrets=False)
                scan(outputs, config["max_files"], config["output_bytes"], exclude_secrets=False)
                scan(checkpoints, config["max_files"], config["checkpoint_bytes"], exclude_secrets=False)
                export_ready_checkpoints(checkpoints, export, exported, config,owner=owners[export])
            except (ValueError, OSError, KeyError) as exc:
                # Invalid/incomplete checkpoints never replace a previously exported snapshot.
                checkpoint_error = type(exc).__name__
        time.sleep(.05)
    _kill_children(signal.SIGTERM)
    try:
        child.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        pass
    _kill_children(signal.SIGKILL)
    child.wait()
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid <= 0: break
        except ChildProcessError:
            break
    try:
        export_ready_checkpoints(checkpoints, export, exported, config,owner=owners[export])
    except (ValueError, OSError, KeyError) as exc:
        checkpoint_error = type(exc).__name__
    result = {"exit_code": child.returncode, "reason": reason, "checkpoint_warning": checkpoint_error}
    if reason is None and child.returncode == 0:
        try:
            result["result_manifest"] = capture(workspace, outputs, export / "results", before, config["outputs"], max_bytes=config["output_bytes"], max_files=config["max_files"])
        except (ValueError, OSError) as exc:
            result["reason"] = "output_capture_failed:" + type(exc).__name__
    handoff_tree(export,owners[export])
    atomic_json(state / "result.json", result,owner=owners[state])
    return 0 if result.get("result_manifest") else 124 if reason else child.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
