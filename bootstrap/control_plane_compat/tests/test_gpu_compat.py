import copy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from test_compat import module, POLICY, stage_variant, assignment

GPU_NODE = 'ec6c1cbe-125d-43e4-bda6-a82b0fcdc34a'
GPU_POLICY = {**copy.deepcopy(POLICY), 'schema_version': 2,
 'node_ids': [GPU_NODE], 'platform': 'linux/amd64', 'accelerator': 'cuda',
 'image_digest': 'campus/python-ml-cuda-v1-amd64@sha256:'+'b'*64,
 'adapter': {'id':'campus.diagnostic.cuda','version':'1'}}
GPU_POLICY['sandbox'].update(memory_bytes=1073741824, pids=128,
 gpu_ids=['0'], vram_bytes=1073741824)

def gpu_stage():
 s,v=stage_variant()
 s.adapter=NS(**GPU_POLICY['adapter'])
 v.image_digest=GPU_POLICY['image_digest']; v.platform='linux/amd64'; v.accelerator='cuda'
 v.resource_requirements.memory_bytes=1073741824
 v.resource_requirements.gpu_slots=1; v.resource_requirements.vram_bytes=1073741824
 node=NS(capabilities={'arch':'amd64','gpus':[{'id':'0','vendor':'NVIDIA',
  'cuda_verified':True,'vram_bytes':8546942976}]})
 return s,v,node

class GPUCompatTest(unittest.TestCase):
 def setUp(self): self.m=module()
 def test_gpu_policy_selects_only_exact_authenticated_node_and_ua(self):
  wrapper={'schema_version':2,'policies':[copy.deepcopy(POLICY),copy.deepcopy(GPU_POLICY)]}
  self.m.validate_policy(wrapper)
  actor=NS(credential=NS(scope='device',node_id=GPU_NODE))
  selected=self.m.policy_for(actor,'campus/0.1.0',wrapper)
  self.assertEqual(selected,GPU_POLICY)
  for scope,node,ua in [('cli',GPU_NODE,'campus/0.1.0'),('device',GPU_NODE,'other'),
                         ('device','00000000-0000-0000-0000-000000000000','campus/0.1.0')]:
   actor.credential.scope=scope; actor.credential.node_id=node
   self.assertIsNone(self.m.policy_for(actor,ua,wrapper))
 def test_gpu_policy_rejects_ambiguous_allowlists_and_unbounded_resources(self):
  for key,value in [('memory_bytes',1073741825),('cpu_millis',2000),('pids',129),
    ('gpu_ids',['1']),('gpu_ids',[0]),('gpu_ids',[True]),('vram_bytes',0),
    ('vram_bytes',1073741825),('workspace_bytes',0),('output_bytes',True)]:
   p=copy.deepcopy(GPU_POLICY); p['sandbox'][key]=value
   with self.assertRaises(ValueError,msg=key): self.m.validate_policy(p)
  for key,value in [('platform','linux/arm64'),('accelerator','cpu')]:
   p=copy.deepcopy(GPU_POLICY); p[key]=value
   with self.assertRaises(ValueError,msg=key): self.m.validate_policy(p)
  for kind in ('node','adapter'):
   p=copy.deepcopy(GPU_POLICY)
   if kind=='node': p['node_ids']=POLICY['node_ids']
   else: p['adapter']=POLICY['adapter']
   with self.assertRaises(ValueError,msg=kind):
    self.m.validate_policy({'schema_version':2,'policies':[copy.deepcopy(POLICY),p]})
 def test_gpu_stage_requires_exact_image_platform_gpu_and_quotas(self):
  s,v,n=gpu_stage(); self.assertTrue(self.m.supports(s,v,GPU_POLICY,node=n))
  for target,key,value in [('variant','image_digest',POLICY['image_digest']),
    ('variant','platform','linux/arm64'),('variant','accelerator','cpu'),
    ('resource','gpu_slots',0),('resource','vram_bytes',1073741823),
    ('stage','network_policy','host'),('stage','bindings',[{}]),
    ('stage','depends_on',['other']),('stage','task_deadline_ms',60001)]:
   s,v,n=gpu_stage(); obj={'variant':v,'resource':v.resource_requirements,'stage':s}[target]
   setattr(obj,key,value); self.assertFalse(self.m.supports(s,v,GPU_POLICY,node=n),key)
  for key,value in [('id','1'),('cuda_verified',False),('vendor','AMD'),('vram_bytes',1)]:
   s,v,n=gpu_stage(); n.capabilities['gpus'][0][key]=value
   self.assertFalse(self.m.supports(s,v,GPU_POLICY,node=n),key)
  s,v,n=gpu_stage(); self.assertFalse(self.m.supports(s,v,GPU_POLICY))
 def test_all_diagnostic_adapters_reserved_but_standard_dto_and_paths_unchanged(self):
  wrapper={'schema_version':2,'policies':[POLICY,GPU_POLICY]}
  with patch.object(self.m,'load_policy',return_value=wrapper):
   s,v,n=gpu_stage(); self.assertFalse(self.m.supports(s,v,None,node=n))
   s,v=stage_variant(); self.assertFalse(self.m.supports(s,v,None))
   s.adapter.id='ordinary'; self.assertTrue(self.m.supports(s,v,None))
  a=assignment(); self.assertIs(self.m.assignment(a,None,remaining_ms=1),a)
  rich=self.m.assignment(a,GPU_POLICY,remaining_ms=59999)
  self.assertEqual(rich['resource_limits']['gpu_ids'],['0'])
  self.assertEqual(rich['resource_limits']['vram_bytes'],1073741824)
  self.assertEqual(rich['outputs'],['model.json'])
  mapped=self.m.result_manifest({'outputs':[{'path':'workspace/model.json','artifact_id':GPU_NODE}]},GPU_POLICY)
  self.assertEqual(mapped['outputs'][0]['path'],'model.json')

if __name__=='__main__': unittest.main()
