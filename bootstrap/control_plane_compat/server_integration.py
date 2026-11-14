"""Disposable-container integration test; creates/drops ONLY a random test schema.

DATABASE_URL must name the existing test-capable PostgreSQL server. All Campus
engines and migrations use options=-csearch_path=test_<uuid>. This test never
queries existing pool data and must never be mistaken for the physical Mac run.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
SERVER = Path(os.environ["TEST_SERVER_ROOT"]).resolve()
sys.path[:0] = [str(SERVER), str(ROOT)]
base = os.environ["DATABASE_URL"]
schema = "test_"+uuid4().hex
if not re.fullmatch(r"test_[a-f0-9]{32}", schema):
    raise RuntimeError("invalid isolated test schema")
separator = "&" if "?" in base else "?"
test_url = base+separator+"options=-csearch_path%3D"+schema
engine = create_engine(base, isolation_level="AUTOCOMMIT")
created = False


def run(temp):
    os.environ["DATABASE_URL"] = test_url
    os.environ["STORAGE_DIR"] = str(temp/"default-storage")
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(SERVER/"alembic.ini")), "head")
    from fastapi.testclient import TestClient
    from campus import models as m, worker_compat
    from campus.auth import issue_credential
    from campus.config import Settings
    from campus.main import create_app, plan_once, verify_once
    gpu = os.environ.get("DIAGNOSTIC_KIND") == "gpu"
    if gpu:
        from run_gpu import plan
    else:
        from run_mac import plan

    catalog=json.loads((ROOT/("combined-catalog.json" if gpu else "mac-catalog.json")).read_text())
    ordinary=copy.deepcopy(catalog["adapters"][-1 if gpu else 0])
    ordinary["ref"]["id"]="ordinary"
    ordinary.pop("recipe")
    catalog["adapters"].append(ordinary)
    catalog_path=temp/"catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    settings=Settings(database_url=test_url, storage_dir=temp/"storage", catalog_file=catalog_path,
                      public_origin="http://127.0.0.1:8000",secure_cookies=False,long_poll_ms=0)
    app=create_app(settings,enable_background=False)
    with TestClient(app,base_url=settings.public_origin) as client:
        runtime=app.state.runtime
        with runtime.sessions.begin() as db:
            actual_schema=db.scalar(text("SELECT current_schema()"))
            assert actual_schema==schema,actual_schema
            pool=m.Pool(name="Isolated compatibility test")
            db.add(pool); db.flush()
            user=m.User(pool_id=pool.id,role="owner",name="Test owner")
            db.add(user); db.flush()
            _,token,_=issue_credential(db,user,"cli")
        owner={"Authorization":"Bearer "+token}

        def call(method,path,body=None,headers=None,status=200,raw=None):
            kwargs={"headers":headers if headers is not None else owner}
            if raw is not None: kwargs["content"]=raw
            elif body is not None: kwargs["json"]=body
            response=client.request(method,"/api/v1"+path,**kwargs)
            assert response.status_code==status,(path,response.status_code,response.text)
            return response.json() if response.content and status!=204 else None

        invite=call("POST","/invitations",{"kind":"device","name":"Isolated arm64"},status=201)
        credential=call("POST","/invitations/redeem",{"token":invite["token"],"installation_id":str(uuid4()),"protocol_version":1},headers={},status=201)
        policy=json.loads((ROOT/("gpu-policy.json" if gpu else "mac-policy.json")).read_text())
        policy["node_ids"]=[credential["node_id"]]
        policy_path=temp/"policy.json"
        configured=({"schema_version":2,"policies":[json.loads((ROOT/"mac-policy.json").read_text()),policy]}
                    if gpu else policy)
        policy_path.write_text(json.dumps(configured))
        os.environ[worker_compat.ENV]=str(policy_path)
        worker_compat.load_policy.cache_clear()
        env=catalog["environments"][-1 if gpu else 0]
        caps={"os":"windows" if gpu else "darwin","arch":"amd64" if gpu else "arm64","cpu_logical":8,"memory_available_bytes":2<<30,
              "disk_available_bytes":1<<30,"runtime":{"type":"docker","version":"integration","healthy":True},
              "gpus":[{"id":"0","vendor":"nvidia","vram_bytes":8<<30,"driver":"integration","cuda_verified":True}] if gpu else [],"power_state":"ac","offered_budget":env["resource_requirements"],
              "preflight_revision":1,"image_digests":[env["image_digest"]],"allow_on_battery":False}
        auth={"Authorization":"Bearer "+credential["token"]}
        session=call("POST","/node-sessions",{"protocol_version":1,"capabilities":caps},auth,status=201)
        device={**auth,"X-Node-Session":session["id"],"User-Agent":"campus/0.1.0"}

        def job(prepared):
            preview=call("POST","/job-previews",{"prepared_plan":prepared},status=202)
            plan_once(runtime)
            preview=call("GET","/job-previews/"+preview["id"])
            assert preview["state"]=="ready",preview
            return call("POST","/jobs",{"preview_id":preview["id"],"plan_revision":preview["plan_revision"]},
                        {**owner,"Idempotency-Key":str(uuid4())},status=201)

        diagnostic=job(plan())
        if gpu:
            bad_caps=copy.deepcopy(caps); bad_caps["gpus"][0]["id"]="1"
            bad_session=call("POST","/node-sessions",{"protocol_version":1,"capabilities":bad_caps},auth,status=201)
            call("POST","/work/next",{"wait_ms":0},{**auth,"X-Node-Session":bad_session["id"],"User-Agent":"campus/0.1.0"},status=204)
            session=call("POST","/node-sessions",{"protocol_version":1,"capabilities":caps},auth,status=201)
            device={**auth,"X-Node-Session":session["id"],"User-Agent":"campus/0.1.0"}
        # Same physical capability, different UA: diagnostic is never assigned.
        call("POST","/work/next",{"wait_ms":0},{**device,"User-Agent":"other"},status=204)
        # Another genuinely enrolled, ready node with the same image also cannot claim it.
        invite2=call("POST","/invitations",{"kind":"device","name":"Not allowlisted"},status=201)
        cred2=call("POST","/invitations/redeem",{"token":invite2["token"],"installation_id":str(uuid4()),"protocol_version":1},headers={},status=201)
        auth2={"Authorization":"Bearer "+cred2["token"]}
        sess2=call("POST","/node-sessions",{"protocol_version":1,"capabilities":caps},auth2,status=201)
        call("POST","/work/next",{"wait_ms":0},{**auth2,"X-Node-Session":sess2["id"],"User-Agent":"campus/0.1.0"},status=204)
        rich=call("POST","/work/next",{"wait_ms":0},device)
        assert 0<rich["task_remaining_ms"]<=60000
        assert rich["resource_limits"]==policy["sandbox"] and rich["outputs"]==["model.json"]
        assert rich["validator_policy"]=={"mode":"structural"}
        if os.environ.get("EXPORT_FIXTURE"):
            fixture=copy.deepcopy(rich)
            fixture["lease_token"]="non-authoritative-test-fixture-lease-token"
            Path(os.environ["EXPORT_FIXTURE"]).write_text(json.dumps(fixture,indent=2)+"\n")
        proof={k:rich[k] for k in ("generation","lease_token")}
        base_attempt="/attempts/"+rich["attempt_id"]
        call("POST",base_attempt+"/started",proof,device)
        content=b'{"integration_fixture":true}'
        sha=hashlib.sha256(content).hexdigest()
        upload=call("POST","/uploads",{"name":"model.json","kind":"output","size_bytes":len(content),
                    "sha256":sha,"part_size_bytes":1024,"attempt_id":rich["attempt_id"],"proof":proof},device,status=201)
        call("PUT","/uploads/"+upload["id"]+"/parts/0",headers={**device,"X-Content-SHA256":sha},raw=content)
        artifact=call("POST","/uploads/"+upload["id"]+"/finalize",headers=device)
        manifest={"outputs":[{"path":"workspace/model.json","artifact_id":artifact["id"]}],"item_ids":[],"compute_ms":1}
        stale={**proof,"lease_token":"z"*43,"manifest":manifest}
        call("POST",base_attempt+"/complete",stale,device,status=409)
        receipt=call("POST",base_attempt+"/complete",{**proof,"manifest":manifest},device,status=202)
        repeated=call("POST",base_attempt+"/complete",{**proof,"manifest":manifest},device,status=202)
        assert repeated==receipt
        verify_once(runtime)
        complete=call("GET","/jobs/"+diagnostic["id"])
        assert complete["state"]=="succeeded" and complete["tasks"][0]["verification_level"]=="structural"
        standard_plan=plan()
        standard_plan["stages"][0]["adapter"]["id"]="ordinary"
        ordinary_job=job(standard_plan)
        standard=call("POST","/work/next",{"wait_ms":0},{**device,"User-Agent":"ordinary-client"})
        assert isinstance(standard["outputs"][0],dict) and isinstance(standard["validator_policy"],str)
        assert "task_remaining_ms" not in standard and "pids" not in standard["resource_limits"]
        assert standard["job_id"]==ordinary_job["id"]
        print(json.dumps({"integration":"passed","schema":schema,"checks":["normal preview validation","non-allowlisted ready node cannot claim","other UA cannot claim diagnostic","rich hard-bound assignment","real upload provenance","stale fence rejected","completion path bridge","idempotent completion","structural acceptance","ordinary DTO unchanged"]}))
        # Credentials/fences are intentionally never exported from the disposable test.
    runtime.engine.dispose() if hasattr(runtime,"engine") else None


try:
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    created=True
    with tempfile.TemporaryDirectory(prefix="campus-compat-test-") as directory:
        run(Path(directory))
finally:
    if created:
        assert re.fullmatch(r"test_[a-f0-9]{32}",schema)
        with engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        print(json.dumps({"cleanup":"dropped isolated schema","schema":schema}))
    engine.dispose()
