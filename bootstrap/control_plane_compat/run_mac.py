"""Prepare or submit a real bounded job and independently verify its artifact.

Credentials stay in the local runtime. No fake worker or completion is used.
Submission requires --submit; the default only writes a reviewable plan.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
from uuid import uuid4

from reference import verify

ROOT = Path(__file__).resolve().parent


def plan():
    catalog = json.loads((ROOT / "mac-catalog.json").read_text())
    environment = catalog["environments"][0]
    variant = {k:v for k,v in environment.items() if k not in {"id","name"}}
    empty = json.dumps({"project_digest":None,"inputs":[]},sort_keys=True,separators=(",",":"))
    code = (ROOT / "fit_model.py").read_text()
    if len(code) > 4096:
        raise ValueError("workload exceeds normal Stage argument limit")
    return {
        "schema_version":"1","plan_revision":1,"project_digest":None,
        "input_manifest_digest":hashlib.sha256(empty.encode()).hexdigest(),
        "execution_kind":"single",
        "stages":[{
            "stage_id":"fit","adapter":{"id":"campus.diagnostic","version":"1"},
            "variants":[variant],"argv":["python","-c",code],"working_dir":"/workspace",
            "bindings":[],"outputs":[{"path":"model.json","required":True,"max_bytes":65536}],
            "network_policy":"none","task_deadline_ms":60000,
            "validation_policy":"structural","resume_policy":"restart","parameters":{}}],
        "job_deadline_at":(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
        "retry_policy":{"max_attempts":1,"backoff_ms":0},
        "assumptions":["Diagnostic CPU fit on the explicitly allowlisted Apple Silicon worker; server structural verification plus independent client numerical reference."]}


class API:
    def __init__(self, runtime, endpoint):
        owner = json.loads((runtime/"owner-cli.json").read_text(encoding="utf-8-sig"))
        self.token = owner["token"]
        self.endpoint = endpoint.rstrip("/")+"/api/v1"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=str(runtime/"campus-local-ca.pem"))))

    def request(self, method, route, body=None, *, key=None, raw=False):
        headers={"Authorization":"Bearer "+self.token,"User-Agent":"campus-diagnostic-review/1"}
        if body is not None:
            headers["Content-Type"]="application/json"
        if key:
            headers["Idempotency-Key"]=key
        data=json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(self.endpoint+route,data=data,method=method,headers=headers)
        try:
            with self.opener.open(request,timeout=15) as response:
                payload=response.read(65537 if raw else 4*1024*1024)
                return (payload,dict(response.headers)) if raw else json.loads(payload)
        except urllib.error.HTTPError as error:
            problem=json.loads(error.read(8192))
            raise RuntimeError(f"{method} {route}: HTTP {error.code}, {problem.get('code')}") from None


def execute(api, output, submitted_plan):
    policy=json.loads((ROOT/"mac-policy.json").read_text())
    node_id=policy["node_ids"][0]
    node=api.request("GET","/nodes/"+node_id)
    caps=node.get("capabilities") or {}
    limits=policy["sandbox"]
    offered=caps.get("offered_budget") or {}
    if (node["state"]!="ready" or caps.get("arch")!="arm64" or caps.get("os")!="darwin"
            or offered.get("cpu_millis",0)<limits["cpu_millis"]
            or offered.get("memory_bytes",0)<limits["memory_bytes"]
            or offered.get("disk_bytes",0)<sum(limits[k] for k in ("workspace_bytes","output_bytes","checkpoint_bytes","tmp_bytes"))):
        raise RuntimeError("allowlisted Mac is not ready with sufficient current offer")
    preview=api.request("POST","/job-previews",{"prepared_plan":submitted_plan})
    end=time.monotonic()+45
    while preview["state"] not in {"ready","failed","cancelled","needs_input"} and time.monotonic()<end:
        time.sleep(.5)
        preview=api.request("GET","/job-previews/"+preview["id"])
    if preview["state"]!="ready":
        raise RuntimeError("normal plan validation did not become ready: "+str(preview.get("error_code")))
    key=str(uuid4())
    job=api.request("POST","/jobs",{"preview_id":preview["id"],"plan_revision":preview["plan_revision"]},key=key)
    evidence={"endpoint":api.endpoint,"node_id":node_id,"preview_id":preview["id"],
              "job_id":job["id"],"idempotency_key":key,"state":job["state"]}
    (output/"submission.json").write_text(json.dumps(evidence,indent=2)+"\n")
    deadline=time.monotonic()+110
    prior=None
    while job["state"] not in {"succeeded","failed","partial","cancelled"} and time.monotonic()<deadline:
        state=(job["state"],tuple(t["state"] for t in job["tasks"]))
        if state!=prior:
            print(json.dumps({"job_id":job["id"],"state":state}),flush=True)
            prior=state
        time.sleep(.5)
        job=api.request("GET","/jobs/"+job["id"])
    (output/"job.json").write_text(json.dumps(job,indent=2)+"\n")
    if job["state"]!="succeeded":
        raise RuntimeError("real job did not succeed; inspect saved job.json: "+job["state"])
    if len(job["tasks"])!=1 or job["tasks"][0]["node_id"]!=node_id:
        raise RuntimeError("result was not executed by the requested physical Mac")
    artifacts=[a for a in job["artifacts"] if a["name"]=="model.json"]
    if len(artifacts)!=1:
        raise RuntimeError("missing or ambiguous real model artifact")
    artifact=artifacts[0]
    data,headers=api.request("GET","/artifacts/"+artifact["id"]+"/content",raw=True)
    digest=hashlib.sha256(data).hexdigest()
    if len(data)>65536 or len(data)!=artifact["size_bytes"] or digest!=artifact["sha256"]:
        raise RuntimeError("downloaded artifact checksum or size differs")
    (output/"model.json").write_bytes(data)
    model=json.loads(data)
    if model.get("system")!="Linux" or model.get("machine") not in {"aarch64","arm64"}:
        raise RuntimeError("worker did not report native ARM Linux container")
    numerical=verify(model)
    evidence.update(state=job["state"],task=job["tasks"][0],artifact=artifact,
                    numerical_verification=numerical,model=model,
                    server_verification="structural",verification_scope="independent numerical reference after authenticated download")
    (output/"report.json").write_text(json.dumps(evidence,indent=2)+"\n")
    print(json.dumps(evidence,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime",type=Path,default=Path(os.environ.get("LOCALAPPDATA","."))/"Campus/server-96bfe6d")
    parser.add_argument("--endpoint",default="https://10.4.146.76:8443")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--submit",action="store_true")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    value=plan()
    (args.output/"plan.json").write_text(json.dumps(value,indent=2)+"\n")
    if args.submit:
        execute(API(args.runtime,args.endpoint),args.output,value)
    else:
        print("Prepared reviewable plan; no live job submitted.")


if __name__=="__main__":
    main()
