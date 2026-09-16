"""Synthetic scheduler binding; never contacts SSH, Slurm or a real model."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kinetic_agents.config import load_config
from kinetic_agents.core.store import Store
from kinetic_agents.execution import jobs
from kinetic_agents.execution.deployment import validate_deployment
from kinetic_agents import runner
from kinetic_agents.core.runtime import verify_task

PROJECT = Path(__file__).resolve().parents[1]


def test_dispatch_uses_new_run_namespace_and_explicit_qualified_image(tmp_path):
    root=tmp_path/'solo-max';(root/'work').mkdir(parents=True)
    (tmp_path/'qualification').mkdir()
    (tmp_path/'qualification/accepted.json').write_text(json.dumps({'status':'PASS','image_sha256':'a'*64}))
    deployment=load_config(PROJECT/'configs/solo.yaml')[1]['deployment']
    store=Store(root/'runtime.sqlite')
    store.initialize({'arm':'solo-max','api_usd':0,'cpu_seconds':512*3600,'wall_seconds':3600,
                      'run_id':'cfx_astra_'+'d'*24,'deployment':deployment})
    remote=jobs.RemoteJobs(root,store,transport=False)
    remote.submit('principal',{'argv':['python','-c','print(1)'],'inputs':[], 'outputs':[], 'minutes':2},'one')
    commands=[]
    def checked(command):
        commands.append(command)
        return '12345' if command.startswith('sbatch') else ''
    with patch.object(jobs,'checked',side_effect=checked),patch.object(jobs,'transfer'):
        remote._dispatch(remote.rows()[0])
    row=remote.rows()[0]
    assert row['status']=='SUBMITTED'
    assert '/kinetic-agents/cfx_astra_' in row['remote']
    request=json.loads((remote.archive/row['id']/'request.json').read_text())
    assert request['image_path']==deployment['container_image']
    assert request['image_sha256']=='a'*64
    script=(remote.archive/row['id']/'job.sh').read_text()
    assert '#SBATCH -N 1' in script and '#SBATCH -n 64' in script and '#SBATCH --job-name=cfx' in script
    assert sum(c.startswith('sbatch') for c in commands)==1


def test_foreign_deployment_paths_rejected():
    d=load_config(PROJECT/'configs/solo.yaml')[1]['deployment']
    with pytest.raises(PermissionError):
        validate_deployment({**d,'search_root':'/public3/home/somebody_else/jobs'})


def test_public_input_under_tmp_cannot_start_budget_or_model(tmp_path):
    root=tmp_path/'solo-max';(root/'task').mkdir(parents=True)
    with patch.object(runner,'load',return_value=(root,{},{})),patch.object(runner.subprocess,'Popen') as popen:
        with pytest.raises(PermissionError):runner.start(tmp_path)
        popen.assert_not_called()
    assert not (root/'runtime.sqlite').exists()
    assert not (root/'launch_intent.json').exists()


def test_extra_shared_input_cannot_leak_into_prepared_task(tmp_path):
    import hashlib
    root=tmp_path/'run';(root/'task').mkdir(parents=True)
    (root/'task/TASK.md').write_bytes(b'task')
    (root/'task/other-results.json').write_text('{}')
    with pytest.raises(PermissionError):
        verify_task(root,{'TASK.md':hashlib.sha256(b'task').hexdigest()})
