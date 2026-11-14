"""Prepare/submit the bounded RTX 5050 task and verify its complete result."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from gpu_reference import verify
from run_mac import API

ROOT=Path(__file__).resolve().parent
OUTPUT_MAX=1 << 20


def plan():
    catalog=json.loads((ROOT/'gpu-catalog.json').read_text())
    environment=catalog['environments'][0]
    variant={k:v for k,v in environment.items() if k not in {'id','name'}}
    empty=json.dumps({'project_digest':None,'inputs':[]},sort_keys=True,separators=(',',':'))
    code=(ROOT/'cuda_matmul.py').read_text()
    if len(code)>4096:
        raise ValueError('workload exceeds normal Stage argument limit')
    return {'schema_version':'1','plan_revision':1,'project_digest':None,
        'input_manifest_digest':hashlib.sha256(empty.encode()).hexdigest(),'execution_kind':'single',
        'stages':[{'stage_id':'cuda-matmul','adapter':{'id':'campus.diagnostic.cuda','version':'1'},
            'variants':[variant],'argv':['python','-c',code],'working_dir':'/workspace',
            'bindings':[],'outputs':[{'path':'model.json','required':True,'max_bytes':OUTPUT_MAX}],
            'network_policy':'none','task_deadline_ms':60000,'validation_policy':'structural',
            'resume_policy':'restart','parameters':{}}],
        'job_deadline_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
        'retry_policy':{'max_attempts':1,'backoff_ms':0},
        'assumptions':['Bounded CUDA computation on the allowlisted RTX 5050; PyTorch allocator cooperatively capped at 1 GiB; structural server verification and independent full integer CPU reference.']}


def ready_node(api, policy):
    node=api.request('GET','/nodes/'+policy['node_ids'][0])
    caps=node.get('capabilities') or {}
    limits=policy['sandbox']; offered=caps.get('offered_budget') or {}
    if (node['state']!='ready' or caps.get('arch')!='amd64' or caps.get('os')!='windows'
            or policy['image_digest'] not in caps.get('image_digests',[])
            or not caps.get('runtime',{}).get('healthy')
            or not any(g.get('id')=='0' and g.get('vendor','').casefold()=='nvidia'
                and g.get('cuda_verified') is True and g.get('vram_bytes',0)>=limits['vram_bytes']
                for g in caps.get('gpus',[]))
            or any(offered.get(k,0)<v for k,v in {
                'cpu_millis':limits['cpu_millis'],'memory_bytes':limits['memory_bytes'],
                'disk_bytes':sum(limits[k] for k in ('workspace_bytes','output_bytes','checkpoint_bytes','tmp_bytes')),
                'gpu_slots':1,'vram_bytes':limits['vram_bytes']}.items())):
        raise RuntimeError('allowlisted RTX 5050 node is not ready with the pinned image and sufficient current offer')
    return node


def execute(api, output, submitted_plan):
    policy=json.loads((ROOT/'gpu-policy.json').read_text())
    node=ready_node(api,policy); node_id=node['id']
    preview=api.request('POST','/job-previews',{'prepared_plan':submitted_plan})
    end=time.monotonic()+45
    while preview['state'] not in {'ready','failed','cancelled','needs_input'} and time.monotonic()<end:
        time.sleep(.5); preview=api.request('GET','/job-previews/'+preview['id'])
    if preview['state']!='ready':
        raise RuntimeError('normal preview validation did not become ready: '+str(preview.get('error_code')))
    key=str(uuid4())
    job=api.request('POST','/jobs',{'preview_id':preview['id'],'plan_revision':preview['plan_revision']},key=key)
    evidence={'endpoint':api.endpoint,'node_id':node_id,'preview_id':preview['id'],
        'job_id':job['id'],'idempotency_key':key,'state':job['state']}
    (output/'submission.json').write_text(json.dumps(evidence,indent=2)+'\n')
    deadline=time.monotonic()+110; prior=None
    while job['state'] not in {'succeeded','failed','partial','cancelled'} and time.monotonic()<deadline:
        state=(job['state'],tuple(t['state'] for t in job['tasks']))
        if state!=prior:
            print(json.dumps({'job_id':job['id'],'state':state}),flush=True); prior=state
        time.sleep(.5); job=api.request('GET','/jobs/'+job['id'])
    (output/'job.json').write_text(json.dumps(job,indent=2)+'\n')
    if job['state']!='succeeded':
        raise RuntimeError('real GPU job did not succeed; inspect saved job.json: '+job['state'])
    if len(job['tasks'])!=1 or job['tasks'][0]['node_id']!=node_id:
        raise RuntimeError('result was not executed by the allowlisted physical RTX node')
    artifacts=[a for a in job['artifacts'] if a['name']=='model.json']
    if len(artifacts)!=1:
        raise RuntimeError('missing or ambiguous GPU model artifact')
    artifact=artifacts[0]
    data,_=api.request('GET','/artifacts/'+artifact['id']+'/content',raw=True)
    digest=hashlib.sha256(data).hexdigest()
    if len(data)>OUTPUT_MAX or len(data)!=artifact['size_bytes'] or digest!=artifact['sha256']:
        raise RuntimeError('downloaded artifact checksum or size differs')
    (output/'model.json').write_bytes(data)
    model=json.loads(data); numerical=verify(model)
    evidence.update(state=job['state'],task=job['tasks'][0],artifact=artifact,
        numerical_verification=numerical,model={k:v for k,v in model.items() if k!='matrix'},
        server_verification='structural',
        verification_scope='full exact integer CPU comparison after authenticated download; genuine CUDA execution, not a performance benchmark',
        vram_bound='cooperative 1 GiB PyTorch allocator ceiling; no device-wide hard VRAM isolation')
    (output/'report.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=Path(os.environ.get('LOCALAPPDATA','.'))/'Campus/server-96bfe6d')
    parser.add_argument('--endpoint',default='https://10.4.146.76:8443')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--submit',action='store_true')
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    value=plan(); (args.output/'plan.json').write_text(json.dumps(value,indent=2)+'\n')
    if args.submit:
        execute(API(args.runtime,args.endpoint,raw_max=OUTPUT_MAX),args.output,value)
    else:
        print('Prepared reviewable GPU plan; no live job submitted.')


if __name__=='__main__': main()
