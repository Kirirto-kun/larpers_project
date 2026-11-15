from pathlib import Path
import importlib.util
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parents[1]/'.git/campus-runner/control-plane-96bfe6d/server/campus'
class PatchTest(unittest.TestCase):
 def test_pristine_patch_builds_valid_server_and_preserves_default_path(self):
  spec=importlib.util.spec_from_file_location('apply_compat',ROOT/'apply.py')
  m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
  changes=m.prepare(SOURCE)
  self.assertEqual(set(changes),{'main.py','jobs/scheduler.py','worker_compat.py'})
  for path,code in changes.items(): compile(code,path,'exec')
  self.assertIn('worker_compat.supports(stage, v, worker_policy, node=node)',changes['jobs/scheduler.py'])
  self.assertIn('remaining_ms=int((deadline - current)',changes['jobs/scheduler.py'])
  self.assertIn('if worker_policy else value',changes['jobs/scheduler.py'])
  self.assertIn('dto.ResultManifest.model_validate',changes['main.py'])
  with tempfile.TemporaryDirectory() as temp:
   dst=Path(temp)
   (dst/'jobs').mkdir()
   for path in ('main.py','jobs/scheduler.py'):
    (dst/path).write_bytes((SOURCE/path).read_bytes())
   (dst/'main.py').write_text('# modified\n'+(dst/'main.py').read_text())
   with self.assertRaises(ValueError): m.prepare(dst)
if __name__=='__main__': unittest.main()
