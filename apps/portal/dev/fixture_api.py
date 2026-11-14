"""Local UI preview only: synthetic data, no computation or external AI. Bind loopback."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import uuid
from pathlib import Path

NODE = {'id':'node-1','name':'Fixture laptop (synthetic)','state':'ready','roles':['compute'],'cpu_logical':8,'memory_available_bytes':8589934592,'gpus':[],'offered_budget':{'cpu_percent':50,'memory_bytes':4294967296,'gpu_allowed':False},'preflight':[{'name':'Docker','ok':True,'message':'Synthetic check — no hardware inspected'}],'last_seen_at':'2026-11-14T06:00:00Z','attempts':[]}
JOB = {'id':'job-1','name':'Fixture document corpus','state':'succeeded','accepted_units':100,'failed_units':0,'total_units':100,'stages':[{'stage_id':'embed','state':'succeeded'}],'artifacts':[{'id':'model-1','name':'model.pt','size_bytes':21,'ready':True}],'attempts':[],'validation_level':'Synthetic fixture — no real validation','metrics':{'example_accuracy':0.91},'index_artifact_id':'index-1'}
PREVIEW = {'id':'preview-1','state':'ready','plan_revision':1,'questions':[],'summary':'Synthetic preview. No code will be executed.','accelerator':'cpu','resume_policy':'restart','outputs':['example model.pt'],'assumptions':['Local UI preview only'],'evidence':['Synthetic fixture'],'data_location':'Local fixture process','duration_ms':None}
BENCHMARK = {'id':'bench-1','experiment_id':'synthetic-example','state':'succeeded','workload':'embeddings','unit':'fragments','input_digest':'sha256:synthetic','node_ids':['node-1'],'concurrency':2,'repetitions':3,'cold':False,'git_sha':'synthetic-not-a-real-run','standalone':[{'node_id':'node-1','rates':[10,11,12],'provenance':'Synthetic local preview'}],'pool_rates':[12,13,14],'single_service_rates':[9,10,11],'timings_ms':{'transfer':100,'queue':20,'start':50,'compute':1000,'verify':200,'aggregate':50},'utilization':[{'node_id':'node-1','cpu_percent':0,'gpu_percent':None,'window_ms':10000,'gaps':1}],'bottleneck':'Synthetic fixture; nothing measured','flags':['SYNTHETIC DATA — not a performance claim']}
JOBS = {'job-1':JOB}
KEYS = {}
UPLOADS = {}
BENCHMARKS = {'bench-1':BENCHMARK}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass  # Do not log invitation or credential payloads.
    def send(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        path=self.path.split('?')[0].removeprefix('/api/v1')
        if path.endswith('/events'):
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.write(b'event: reset\ndata: {}\n\n');return
        if path.startswith('/artifacts/'):
            self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="model.pt"');self.end_headers();self.wfile.write(b'SYNTHETIC MODEL FILE\n');return
        if path=='/auth/me':return self.send({'name':'FIXTURE — Local preview','role':'owner','csrf_token':'fixture-only'})
        if path=='/recipes':return self.send([{'id':'embeddings','name':'Document search'},{'id':'training','name':'Image classifier'}])
        if path=='/nodes':return self.send([NODE])
        if path=='/pool/summary':return self.send({'accepted_units':100,'queued_tasks':2,'running_tasks':0,'recovering_tasks':0,'events':[]})
        if path=='/credits':return self.send({'unit_version':'fixture','accepted_units':100,'credits':10,'currency':'CNY','costs':[{'name':'Energy','amount':None,'estimated':True,'assumption':'No physical measurements'}]})
        if path=='/jobs':return self.send(list(JOBS.values()))
        if path=='/benchmarks':return self.send(list(BENCHMARKS.values()))
        if path.endswith('/logs'):return self.send({'lines':['Synthetic preview: no actual computation.'],'truncated':False})
        if path.startswith('/jobs/'):return self.send(JOBS.get(path.split('/')[2],JOB))
        if path.startswith('/job-previews/'):return self.send(PREVIEW)
        if path.startswith('/uploads/'):
            upload=UPLOADS.get(path.split('/')[2],{});return self.send({'part_size_bytes':1048576,'uploaded_parts':upload.get('parts',[])})
        if path.startswith('/benchmarks/'):return self.send(BENCHMARKS.get(path.split('/')[2],BENCHMARK))
        self.send({'code':'NOT_FOUND','message':'Unknown fixture endpoint'},404)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}');path=self.path.split('?')[0].removeprefix('/api/v1')
        if path=='/jobs':
            key=self.headers.get('Idempotency-Key');identifier=KEYS.get(key)
            if not identifier:
                identifier=str(uuid.uuid4());KEYS[key]=identifier;JOBS[identifier]={**JOB,'id':identifier}
            return self.send(JOBS[identifier])
        if path=='/job-previews':return self.send(PREVIEW,202)
        if path.endswith('/answers'):return self.send(PREVIEW)
        if path=='/invitations':return self.send({'command':'echo "FIXTURE ONLY — connect a real coordinator before joining"'})
        if path=='/uploads':
            identifier=str(uuid.uuid4());UPLOADS[identifier]={'parts':[]};return self.send({'id':identifier})
        if path.endswith('/finalize'):return self.send({'artifact_id':'input-'+path.split('/')[2]})
        if path.endswith('/search'):
            identifier=str(uuid.uuid4());JOBS[identifier]={**JOB,'id':identifier,'matches':[{'item_id':'example','source':'Fixture source.txt','excerpt':'Synthetic source passage — not a real search result.','score':0.8}]};return self.send({'search_job_id':identifier},202)
        if path=='/benchmarks':
            identifier=str(uuid.uuid4());BENCHMARKS[identifier]={**BENCHMARK,**data,'id':identifier,'experiment_id':identifier};return self.send(BENCHMARKS[identifier],202)
        if path.endswith('/cancel') and path.startswith('/jobs/'):
            identifier=path.split('/')[2]
            if identifier in JOBS:JOBS[identifier]['state']='cancelled'
        if path.endswith('/control'):NODE['state']={'drain':'draining','stop':'paused','resume':'ready','quarantine':'quarantined','release':'ready'}.get(data.get('action'),'ready')
        return self.send({})
    def do_PUT(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)));parts=self.path.split('/');identifier=parts[4];part=int(parts[-1]);UPLOADS.setdefault(identifier,{'parts':[]})['parts'].append(part);self.send({})
if __name__=='__main__':
    print('SYNTHETIC UI FIXTURE API at http://127.0.0.1:8000 — no real compute, AI or measurements',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
