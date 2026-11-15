import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class CalculationTest(unittest.TestCase):
 def test_bounded_worker_model_passes_independent_exact_reference_and_corruption_fails(self):
  spec=importlib.util.spec_from_file_location('reference',ROOT/'reference.py')
  m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory() as temp:
   result=subprocess.run([sys.executable,str(ROOT/'fit_model.py')],cwd=temp,capture_output=True,text=True,timeout=30)
   self.assertEqual(result.returncode,0,result.stderr)
   import json
   model=json.loads((Path(temp)/'model.json').read_text())
   report=m.verify(model)
   self.assertTrue(report['accepted'])
   self.assertLess(report['max_coefficient_error'],1e-8)
   model['coefficients'][0]+=0.01
   with self.assertRaises(ValueError): m.verify(model)
if __name__=='__main__': unittest.main()
