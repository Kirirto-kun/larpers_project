import json
from pathlib import Path
from environments.prepare import stage_context


def test_image_context_contains_only_runtime_sources_and_verified_wheelhouse(tmp_path):
    root=tmp_path/'source'; destination=tmp_path/'context'
    for relative in ('workloads/main.py','workloads/.env','workloads/.venv/private','sdk/python/campus/client.py',
                     'environments/guard/guard.py','environments/offline.py','environments/prepare_wheelhouse.py','environments/cpu/Dockerfile'):
        path=root/relative; path.parent.mkdir(parents=True,exist_ok=True);path.write_text('secret' if '.env' in relative else 'source')
    stage_context(root,destination,'cpu',None,'linux/amd64')
    assert (destination/'environments/offline.py').is_file()
    assert (destination/'workloads/main.py').is_file()
    assert not (destination/'workloads/.env').exists()
    assert not (destination/'workloads/.venv').exists()
    wheelhouse=json.loads((destination/'environments/wheelhouse/manifest.json').read_text())
    assert wheelhouse['files']==[]
    assert wheelhouse['platform']=='manylinux_2_28_x86_64'
