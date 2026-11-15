"""Explicit online preparation. Emits an allowlist only after a successful build.

Never call from a worker task/probe; ordinary execution is always offline.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def stage_context(root,context,accelerator,wheelhouse,platform):
    root=Path(root).resolve();context=Path(context)
    ignored=shutil.ignore_patterns('.venv','venv','__pycache__','.pytest_cache','*.pyc','*.egg-info','models','wheelhouse','test-results','tests','.git','.env','.env.*','*.key','*.pem','credentials.json')
    count=0;size=0
    def copy(source,destination):
        nonlocal count,size
        source=Path(source)
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('unsafe source in image context')
        count+=1;size+=source.stat().st_size
        if count>10000 or size>256<<20: raise ValueError('runtime source context exceeds quota')
        return shutil.copy2(source,destination)
    for relative in ('workloads','sdk/python','environments/guard','environments/'+accelerator):
        source=root/relative
        for parent,dirs,files in os.walk(source,followlinks=False):
            excluded=ignored(parent,dirs+files)
            dirs[:]=[d for d in dirs if d not in excluded]
            if any((Path(parent)/d).is_symlink() for d in dirs): raise ValueError('symlink directory in image source')
        shutil.copytree(source,context/relative,ignore=ignored,copy_function=copy)
    for name in ('offline.py','prepare_wheelhouse.py'):
        copy(root/'environments'/name,context/'environments'/name)
    expected='manylinux_2_28_'+('x86_64' if platform=='linux/amd64' else 'aarch64')
    target=context/'environments/wheelhouse';target.mkdir()
    if wheelhouse is None:
        manifest={'schema_version':'1','kind':'campus-wheelhouse','platform':expected,'python_version':'3.11','requirements':[],'files':[]}
        raw=json.dumps(manifest,sort_keys=True,indent=2).encode()
        (target/'manifest.json').write_bytes(raw)
    else:
        if __package__:
            from .offline import verify_wheelhouse
        else:
            from offline import verify_wheelhouse
        records=verify_wheelhouse(wheelhouse)
        source=Path(wheelhouse);raw=(source/'manifest.json').read_bytes();manifest=json.loads(raw)
        if manifest['platform']!=expected:raise ValueError('wheelhouse platform does not match image')
        for record,_ in records:shutil.copyfile(source/record['filename'],target/record['filename'])
        (target/'manifest.json').write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--accelerator", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], default="linux/amd64")
    parser.add_argument("--out", default="environments/resolved-profiles.json")
    parser.add_argument("--wheelhouse", help="explicit prepared immutable wheelhouse directory")
    args = parser.parse_args()
    if args.accelerator == "cuda" and args.platform != "linux/amd64":
        parser.error("the CUDA profile currently supports linux/amd64 only")
    root = Path(__file__).resolve().parents[1]
    source = json.loads((root / "environments/profiles.json").read_text())
    profile = next(p for p in source["profiles"] if p["accelerator"] == args.accelerator)
    profile["platform"] = args.platform
    profile["id"] += "-" + args.platform.split("/")[1]
    # Stage only source and lockfiles. Never send .venv, model/data caches or .git
    # to the daemon, including when the user's project contains such directories.
    with tempfile.TemporaryDirectory(prefix="campus-image-") as temporary:
        context = Path(temporary)
        profile['wheelhouse_digest']=stage_context(root,context,args.accelerator,args.wheelhouse,args.platform)
        tag = "campus/" + profile["id"] + ":0.1.0"
        subprocess.run(["docker", "build", "--platform", args.platform, "--file", str(context / profile["dockerfile"]), "--tag", tag, str(context)], check=True)
        inspect = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
    if inspect["Os"] + "/" + inspect["Architecture"] != args.platform or inspect["Config"]["Labels"].get("io.campus.guard") != "1":
        raise RuntimeError("built image does not satisfy the guard/platform contract")
    profile["image_digest"] = inspect["Id"]
    out = root / args.out
    existing = {"schema_version": "1", "profiles": []}
    if out.exists():
        existing = json.loads(out.read_text())
    existing["profiles"] = [p for p in existing["profiles"] if p["id"] != profile["id"]] + [profile]
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(".tmp")
    staging.write_text(json.dumps(existing, indent=2) + "\n")
    staging.replace(out)
    print(f"Prepared {profile['id']}: {profile['image_digest']}")


if __name__ == "__main__":
    main()
