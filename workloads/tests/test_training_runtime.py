"""Check environment ordering in fresh processes, before any torch import."""
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("existing,expected", [(None, ":4096:8"), (":4096:8", ":4096:8"), (":16:8", ":16:8")])
def test_training_initializes_approved_cublas_before_torch_import(existing, expected):
    result = _probe_training_import(existing)
    assert result["torch_import_values"] == [expected]
    assert result["environment"] == expected
    assert result["error_type"] == "StopTorchImport"


@pytest.mark.parametrize("existing", ["", "4096:8", ":4096:2", "unapproved"])
def test_training_rejects_unsupported_cublas_before_torch_import(existing):
    result = _probe_training_import(existing)
    assert result["torch_import_values"] == []
    assert result["error_type"] == "ValueError"
    assert "CUBLAS_WORKSPACE_CONFIG" in result["error"]
    assert ":4096:8" in result["error"] and ":16:8" in result["error"]
    assert result["environment"] == existing


def _probe_training_import(existing):
    env = dict(os.environ)
    env.pop("CAMPUS_RESUME_FROM", None)
    if existing is None:
        env.pop("CUBLAS_WORKSPACE_CONFIG", None)
    else:
        env["CUBLAS_WORKSPACE_CONFIG"] = existing
    code = '''
import importlib.abc, json, os, sys
values = []
class StopTorchImport(RuntimeError): pass
class TorchBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "torch":
            values.append(os.environ.get("CUBLAS_WORKSPACE_CONFIG"))
            raise StopTorchImport("stopped at the actual first torch import")
sys.meta_path.insert(0, TorchBoundary())
try:
    import workloads.training.run
    from workloads.training.adapter import train
    train(None)
except Exception as error:
    result = {"error_type": type(error).__name__, "error": str(error)}
else:
    result = {"error_type": None, "error": ""}
result.update(environment=os.environ.get("CUBLAS_WORKSPACE_CONFIG"), torch_import_values=values)
print(json.dumps(result))
'''
    completed = subprocess.run([sys.executable, "-c", code], env=env,
                               text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)
