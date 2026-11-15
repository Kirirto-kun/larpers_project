"""The optional current Python DTO must work outside the coordinator package."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PythonGenerationTests(unittest.TestCase):
    def test_optional_current_module_imports_alone_and_preserves_project_validation(self):
        pinned = ROOT / "sdk/python/campus/generated/api.py"
        pinned_digest = hashlib.sha256(pinned.read_bytes()).hexdigest() if pinned.is_file() else None
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "current_api.py"
            generated = subprocess.run(
                [sys.executable, str(ROOT / "contracts/generate.py"), "--target", "python", "--out", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            # -I excludes this repository from import search paths. The generated
            # file is loaded as a top-level module, with no campus package context.
            script = """
import copy, importlib.util, json, sys
spec = importlib.util.spec_from_file_location('current_api', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
valid = {'schema_version': '1', 'name': 'one model', 'data': ['dataset/*.npz'], 'stages': [
    {'id': 'fit', 'kind': 'map', 'inputs': ['dataset/*.npz'],
     'command': ['python', 'worker.py', '{input}', '{accelerator}'],
     'outputs': [{'path': 'partial.json', 'max_bytes': 1024}]},
    {'id': 'model', 'kind': 'reduce', 'depends_on': ['fit'],
     'command': ['python', 'worker.py', 'merge', '{accelerator}'],
     'outputs': [{'path': 'model.json', 'max_bytes': 1024}]}]}
validated = module.ProjectConfig.model_validate(valid)
request = module.PreviewRequest.model_validate({'project_config': valid})
assert request.project_config.model_dump() == validated.model_dump()
invalid = []
for path in ('../outside.npz', '.env', 'dataset/CON.npz', 'stages/*.npz', 'dataset/a\\\\b.npz'):
    case = copy.deepcopy(valid); case['data'] = [path]; invalid.append(case)
case = copy.deepcopy(valid); case['stages'][0]['resources'] = {'cpu_millis': True}; invalid.append(case)
case = copy.deepcopy(valid); case['stages'][0]['command'] += ['{unknown}']; invalid.append(case)
case = copy.deepcopy(valid); case['stages'][0]['depends_on'] = ['model']; invalid.append(case)
case = copy.deepcopy(valid); case['stages'][1]['command'] += ['{input}']; invalid.append(case)
case = copy.deepcopy(valid); case['stages'][0]['outputs'] += [{'path': 'PARTIAL.json', 'max_bytes': 1}]; invalid.append(case)
case = copy.deepcopy(valid); case['data'] = ['dataset/A.npz', 'dataset/a.npz']; invalid.append(case)
rejections = 0
for case in invalid:
    try: module.ProjectConfig.model_validate(case)
    except ValueError: rejections += 1
    else: raise AssertionError('unsafe project accepted: ' + repr(case))
assert not any(name == 'campus' or name.startswith('campus.') for name in sys.modules)
print(json.dumps({'validated': validated.model_dump(), 'schema': module.ProjectConfig.model_json_schema(), 'rejections': rejections}))
"""
            isolated = subprocess.run([sys.executable, "-I", "-c", script, str(output)],
                                      capture_output=True, text=True, check=False)
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            actual = json.loads(isolated.stdout)
            self.assertEqual(actual["rejections"], 11)
            sys.path.insert(0, str(ROOT / "server"))
            try:
                from campus.project_schema import ProjectConfig

                self.assertEqual(actual["schema"], ProjectConfig.model_json_schema())
                self.assertEqual(actual["validated"], ProjectConfig.model_validate(actual["validated"]).model_dump())
            finally:
                sys.path.pop(0)
        if pinned_digest is None:
            self.assertFalse(pinned.exists(), "--out unexpectedly created the optional pinned SDK module")
        else:
            self.assertEqual(hashlib.sha256(pinned.read_bytes()).hexdigest(), pinned_digest)


if __name__ == "__main__":
    unittest.main()
