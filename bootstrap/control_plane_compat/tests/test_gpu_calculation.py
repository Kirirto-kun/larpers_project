import importlib.util
import struct
import hashlib
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class GPUCalculationTest(unittest.TestCase):
 def test_full_matrix_verification_rejects_one_modified_entry_and_false_cuda(self):
  spec=importlib.util.spec_from_file_location('gpu_reference',ROOT/'gpu_reference.py')
  m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
  # Independent manually derived rows: the modular inputs repeat every 8.
  expected=[]
  for i in range(256):
   expected.append([32*sum(((i*3+k*5+1)%8)*((k*7+j*2+3)%8) for k in range(8)) for j in range(256)])
  flat=[v for row in expected for v in row]
  digest=hashlib.sha256(struct.pack('<65536i',*flat)).hexdigest()
  result={'system':'Linux','machine':'x86_64','cuda_available':True,
   'device_name':'NVIDIA GeForce RTX 5050','device_index':0,'compute_capability':[12,0],
   'torch_version':'2.9.1+cu128','cuda_version':'12.8','dimension':256,'repetitions':64,
   'matrix':expected,'matrix_sha256':digest,'max_memory_allocated':786432,
   'max_memory_reserved':2097152,'allocator_limit_bytes':1073741824}
  self.assertTrue(m.verify(result)['accepted'])
  result['matrix'][127][129]+=1
  with self.assertRaises(ValueError): m.verify(result)
  result['matrix'][127][129]-=1; result['cuda_available']=False
  with self.assertRaises(ValueError): m.verify(result)
  result['cuda_available']=True; result['matrix'][127][129]+=.5
  with self.assertRaises(ValueError): m.verify(result)

if __name__=='__main__': unittest.main()
