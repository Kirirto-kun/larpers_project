from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
NODE = '50b752cf-f13f-4f76-ae0c-0b23f7666d35'
IMAGE = 'campus/python-ml-cpu-v1-arm64@sha256:' + 'a'*64
SANDBOX = dict(cpu_millis=1000, memory_bytes=268435456, pids=64,
 workspace_bytes=16777216, output_bytes=1048576, checkpoint_bytes=1048576,
 tmp_bytes=16777216, gpu_ids=[], vram_bytes=0)
POLICY = dict(schema_version=1, node_ids=[NODE], image_digest=IMAGE,
 adapter={'id':'campus.diagnostic','version':'1'}, max_task_ms=60000,
 sandbox=SANDBOX)

def module():
 spec=importlib.util.spec_from_file_location('diagnostic_compat',ROOT/'worker_compat.py')
 obj=importlib.util.module_from_spec(spec); spec.loader.exec_module(obj); return obj

def assignment():
 return dict(generation=1,lease_token='x'*43,attempt_id=NODE,task_id=NODE,job_id=NODE,
 stage_id='fit',variant_id='cpu',lease_remaining_ms=60000,heartbeat_interval_ms=10000,
 task_deadline_at='2026-11-14T12:00:00Z',manifest_digest='a'*64,
 adapter=POLICY['adapter'],image_digest=IMAGE,platform='linux/arm64',accelerator='cpu',
 argv=['python','-c','print(1)'],working_dir='/workspace',inputs=[],
 outputs=[dict(path='model.json',required=True,max_bytes=65536)],
 resource_limits=dict(cpu_millis=1000,memory_bytes=268435456,disk_bytes=35651584,gpu_slots=0,vram_bytes=0),
 network_policy='none',resume_checkpoint=None,validator_policy='structural',item_ids=[],parameters={})

def stage_variant():
 a=assignment()
 stage=SimpleNamespace(**{**a,'bindings':[],'depends_on':[], 'task_deadline_ms':60000,'resume_policy':'restart'})
 variant=SimpleNamespace(adapter=a['adapter'],image_digest=IMAGE,platform='linux/arm64',accelerator='cpu',resource_requirements=SimpleNamespace(**a['resource_limits']))
 stage.adapter=SimpleNamespace(**a['adapter']); return stage,variant

class CompatTest(unittest.TestCase):
 def setUp(self): self.m=module()
 def test_only_allowlisted_authenticated_device_and_exact_legacy_ua(self):
  p=self.m.validate_policy(copy.deepcopy(POLICY))
  actor=SimpleNamespace(credential=SimpleNamespace(scope='device',node_id=NODE))
  self.assertIs(self.m.policy_for(actor,'campus/0.1.0',p),p)
  for scope,node,ua in [('cli',NODE,'campus/0.1.0'),('device',NODE,'other'),('device','00000000-0000-0000-0000-000000000000','campus/0.1.0')]:
   actor.credential.scope=scope; actor.credential.node_id=node
   self.assertIsNone(self.m.policy_for(actor,ua,p))
 def test_invalid_trusted_quotas_fail_closed(self):
  for field,value in [('pids',0),('workspace_bytes',0),('memory_bytes',1024),('cpu_millis',2000),('gpu_ids',['GPU-1']),('vram_bytes',1)]:
   p=copy.deepcopy(POLICY); p['sandbox'][field]=value
   with self.assertRaises(ValueError): self.m.validate_policy(p)
 def test_only_exact_diagnostic_stage_is_leased(self):
  s,v=stage_variant(); self.assertTrue(self.m.supports(s,v,POLICY))
  for key,value in [('bindings',[{}]),('depends_on',['other']),('resume_policy','checkpoint'),('task_deadline_ms',60001),('working_dir','/workspace/sub'),('network_policy','host')]:
   s,v=stage_variant(); setattr(s,key,value); self.assertFalse(self.m.supports(s,v,POLICY),key)
  s,v=stage_variant(); s.outputs=[SimpleNamespace(path='nested/model.json',required=True,max_bytes=65536)]
  self.assertFalse(self.m.supports(s,v,POLICY))
  s,v=stage_variant(); v.resource_requirements.disk_bytes-=1; self.assertFalse(self.m.supports(s,v,POLICY))
  s,v=stage_variant()
  with patch.object(self.m,'load_policy',return_value=POLICY):
   self.assertFalse(self.m.supports(s,v,None))
   s.adapter.id='ordinary'; self.assertTrue(self.m.supports(s,v,None))
 def test_relative_task_bound_and_rich_shapes(self):
  a=assignment(); original=copy.deepcopy(a)
  rich=self.m.assignment(a,POLICY,remaining_ms=59876)
  self.assertEqual(rich['task_remaining_ms'],59876)
  self.assertEqual(rich['resource_limits'],SANDBOX)
  self.assertEqual(rich['outputs'],['model.json'])
  self.assertEqual(rich['validator_policy'],{'mode':'structural'})
  self.assertEqual(a,original)
  self.assertIs(self.m.assignment(a,None,remaining_ms=59876),a)
  for n in (0,-1,60001):
   with self.assertRaises(ValueError): self.m.assignment(a,POLICY,remaining_ms=n)
 def test_completion_names_map_before_normal_fenced_validation(self):
  manifest={'outputs':[{'path':'workspace/model.json','artifact_id':NODE}],'item_ids':[],'compute_ms':123}
  original=copy.deepcopy(manifest)
  mapped=self.m.result_manifest(manifest,POLICY)
  self.assertEqual(mapped['outputs'][0]['path'],'model.json'); self.assertEqual(manifest,original)
  for name in ('model.json','workspace/nested/model.json','outputs/model.json','workspace/../model.json'):
   bad=copy.deepcopy(manifest); bad['outputs'][0]['path']=name
   with self.assertRaises(ValueError): self.m.result_manifest(bad,POLICY)
  self.assertIs(self.m.result_manifest(manifest,None),manifest)

if __name__=='__main__': unittest.main()
