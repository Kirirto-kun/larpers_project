"""Real POSIX UID/file-type regression checks; run as root in an isolated test OS.

All filesystem changes stay in one tempfile directory. No host configuration or
container daemon is changed. The UID subprocesses execute fixed test code only.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import tempfile

repo=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(repo),str(repo/'sdk/python')]


def as_uid(uid, argv):
    def drop():
        os.setgroups([]); os.setgid(uid); os.setuid(uid)
    return subprocess.run([sys.executable,'-c',*argv],preexec_fn=drop,capture_output=True,text=True,timeout=5)


def permissions():
    from environments.guard.guard import prepare_shared_mounts, handoff_tree, atomic_json
    from environments.guard.files import capture
    with tempfile.TemporaryDirectory(prefix='campus-permissions-') as temporary:
        root=Path(temporary); root.chmod(0o711)
        state=root/'state'; export=root/'export'; inputs=root/'inputs'
        for directory in (state,export,inputs):
            directory.mkdir(mode=0o700); os.chown(directory,1000,1000)
        workload_uid,owners=prepare_shared_mounts(state,export)
        assert workload_uid not in {0,1000}
        inputs.chmod(0o555); (inputs/'data.txt').write_text('approved-input'); (inputs/'data.txt').chmod(0o444)
        assert as_uid(workload_uid,[f"from pathlib import Path; assert Path({str(inputs/'data.txt')!r}).read_text()=='approved-input'"]).returncode==0
        atomic_json(state/'challenge.json',{'challenge':'trusted'},owner=owners[state])
        assert as_uid(1000,[f"from pathlib import Path; assert 'trusted' in Path({str(state/'challenge.json')!r}).read_text()"]).returncode==0
        denied=as_uid(workload_uid,[f"from pathlib import Path; Path({str(state/'challenge.json')!r}).read_text()"])
        assert denied.returncode!=0,'workload could read/write supervisor state'
        workspace=root/'workspace'; outputs=root/'outputs'; workspace.mkdir(); outputs.mkdir()
        (workspace/'model.pt').write_bytes(b'weights')
        capture(workspace,outputs,export/'results',{},[],max_bytes=1024)
        handoff_tree(export,owners[export])
        allowed=as_uid(1000,[f"from pathlib import Path; assert Path({str(export/'results/workspace/model.pt')!r}).read_bytes()==b'weights'"])
        assert allowed.returncode==0,allowed.stderr
        denied=as_uid(workload_uid,[f"from pathlib import Path; Path({str(export/'results/workspace/model.pt')!r}).write_bytes(b'forged')"])
        assert denied.returncode!=0,'workload could forge protected export'
        # A host installation whose UID equals the default workload UID must work too.
        for directory in (state,export): os.chown(directory,65532,65532)
        alternative,_=prepare_shared_mounts(state,export)
        assert alternative not in {0,65532}
    print('PASS POSIX host UID1000 and UID65532 ownership, read-only inputs, isolated exports')


def fifo():
    with tempfile.TemporaryDirectory(prefix='campus-checkpoint-fifo-') as temporary:
        root=Path(temporary); snapshot=root/'checkpoints'/'step'; snapshot.mkdir(parents=True)
        (snapshot/'READY').write_text('complete'); os.mkfifo(snapshot/'manifest.json')
        code='''import sys
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from environments.guard.guard import export_ready_checkpoints
try:
 export_ready_checkpoints(Path(sys.argv[3]),Path(sys.argv[4]),set(),{'max_files':100,'checkpoint_bytes':1048576})
except (ValueError,OSError):
 raise SystemExit(0)
raise SystemExit('unsafe checkpoint was accepted')
'''
        child=subprocess.run([sys.executable,'-c',code,str(repo),str(repo/'sdk/python'),str(root/'checkpoints'),str(root/'export')],timeout=3,capture_output=True,text=True)
        assert child.returncode==0,child.stderr
    print('PASS workload checkpoint FIFO rejected without blocking watchdog')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--case',choices=['permissions','fifo'],required=True)
    args=parser.parse_args()
    if args.case=='permissions':
        if os.name!='posix' or os.getuid()!=0: raise SystemExit('permission check requires POSIX test root')
        permissions()
    else: fifo()
