"""Public synthetic tests: do not depend on local benchmark/account files."""

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from kinetic_agents import config, runner, release
from kinetic_agents.core.inputs import task_directory
from kinetic_agents.core.runtime import verify_task
from kinetic_agents.evaluation.pins import source_pins

PROJECT = Path(__file__).resolve().parents[1]


def fixture_inputs(tmp_path):
    task = tmp_path / "inputs"
    task.mkdir()
    (task / "TASK.md").write_bytes((PROJECT / "tasks/usc_ii/TASK.md").read_bytes())
    (task / "parent.yaml").write_bytes(b"synthetic-parent-only\n")
    accepted = tmp_path / "accepted.json"
    accepted.write_text(json.dumps({"status": "PASS", "image_sha256": "a" * 64}))
    cases = [
        {
            "case_id": str(i),
            "operator": {"family": "ignition_delay" if i < 320 else "laminar_flame_speed"},
        }
        for i in range(610)
    ]
    pool = tmp_path / "evaluation.json"
    pool.write_text(
        json.dumps(
            {
                "development": cases[:491],
                "recheck": cases[491:],
                "source_pins": source_pins(),
                "solver_version": "synthetic",
            }
        )
    )
    deployment = config.load_config(PROJECT / "configs/solo.yaml")[1]["deployment"]
    kwargs = dict(
        cpu_hours=512,
        wall_hours=24,
        evaluation_hours=64,
        auth_home=tmp_path / "host-auth",
        task=task,
        accepted=accepted,
        evaluation_base=pool,
        deployment=deployment,
    )
    return task, kwargs


def prepare_fixture(tmp_path, name="run", mixed=False, expert=False):
    task, kwargs = fixture_inputs(tmp_path)
    if expert:
        (task / "TASK.md").write_bytes((PROJECT / "tasks/usc_ii_expert_v2/TASK.md").read_bytes())
    run = tmp_path / name
    with patch.object(
        runner, "PARENT_SHA", hashlib.sha256((task / "parent.yaml").read_bytes()).hexdigest()
    ), patch.object(runner, "cli_version", return_value=runner.CLI), patch.dict(
        sys.modules, {"cantera": SimpleNamespace(__version__="synthetic")}
    ):
        result = runner.prepare(
            run,
            **kwargs,
            **({"researcher_model": "gpt-5.5", "researcher_effort": "high"} if mixed else {})
        )
        runner.load(run)
    return run, task, result


def test_profiles_reference_one_task_and_equal_resources():
    solo = config.load_config(PROJECT / "configs/solo.yaml")[1]
    team = config.load_config(PROJECT / "configs/team.yaml")[1]
    for key in ("inputs", "resources", "account", "deployment", "transcript"):
        assert solo[key] == team[key]
    assert solo["inputs"]["task"] == str(PROJECT / "tasks/usc_ii")
    assert solo["model"]["max_agents"] == 1 and team["model"]["max_agents"] == 3
    assert solo["model"]["name"] == team["model"]["name"] == "gpt-6-astra"
    assert config.new_run_path(solo) != config.new_run_path(solo)


@pytest.mark.parametrize("mixed", [False, True])
def test_expert_task_is_pinned_in_contract_and_manifest(tmp_path, mixed):
    run, task, result = prepare_fixture(tmp_path, mixed=mixed, expert=True)
    digest = hashlib.sha256(runner.read_task(task)).hexdigest()
    plan = json.loads((run / "preflight.json").read_text())
    contract = next(iter(plan["contracts"].values()))
    assert digest != runner.TASK_SHA
    assert result["task_sha256"] == contract["task_sha256"] == plan["task_manifest"]["TASK.md"] == digest
    (task / "TASK.md").write_bytes((PROJECT / "tasks/usc_ii/TASK.md").read_bytes())
    with pytest.raises(PermissionError):
        verify_task(runner.run_root(run), plan["task_manifest"])


def test_expert_profiles_share_only_approved_supplement():
    solo = config.load_config(PROJECT / "configs/expert-v2-solo.yaml")[1]
    team = config.load_config(PROJECT / "configs/expert-v2-team.yaml")[1]
    old = config.load_config(PROJECT / "configs/solo.yaml")[1]
    for key in ("inputs", "resources", "account", "deployment", "transcript"):
        assert solo[key] == team[key]
    assert solo["resources"] == old["resources"]
    original = (PROJECT / "tasks/usc_ii/TASK.md").read_text().rstrip()
    enhanced = runner.read_task(PROJECT / "tasks/usc_ii_expert_v2").decode()
    assert enhanced.startswith(original + "\n\n### Domain-expert guidance")
    draft = (PROJECT / "docs/TASK_EXPERT_GUIDANCE_V2_DRAFT.md").read_text()
    supplement = draft.split("## Exact proposed supplement (option B)\n\n")[1].split("\n## What is intentionally not included")[0].strip()
    assert enhanced == original + "\n\n" + supplement + "\n"


def test_unregistered_task_is_rejected(tmp_path):
    (tmp_path / "TASK.md").write_text("Unapproved scientific change")
    with pytest.raises(PermissionError):
        runner.read_task(tmp_path)


@pytest.mark.parametrize("mixed", [False, True])
def test_preparation_references_input_no_task_copy_or_execution(tmp_path, mixed):
    run, task, result = prepare_fixture(tmp_path, mixed=mixed)
    root = runner.run_root(run)
    assert task_directory(root) == task
    assert not (root / "task").exists()
    assert not (root / "runtime.sqlite").exists()
    assert not (root / "launch_intent.json").exists()
    assert not list((root / "work").iterdir())
    assert result["observer_required"] is False
    plan = json.loads((run / "preflight.json").read_text())
    assert root.name + "/input-reference.json" in plan["capsule_pins"]
    verify_task(root, plan["task_manifest"])
    (task / "TASK.md").write_text("changed task")
    with pytest.raises(PermissionError):
        verify_task(root, plan["task_manifest"])


def test_freeze_is_exact_and_no_old_packages(tmp_path):
    target = tmp_path / "frozen"
    snapshot = release.build(target)
    assert len(snapshot["files"]) > 20
    assert all(name.startswith("kinetic_agents/") for name in snapshot["files"])
    assert not any(
        "local/" in name or "runs/" in name or "parent.yaml" in name for name in snapshot["files"]
    )
    (target / "kinetic_agents/cli.py").chmod(0o600)
    (target / "kinetic_agents/cli.py").write_text("# tampered")
    with pytest.raises(PermissionError):
        release.verify(target)


def test_profile_cannot_silently_override_common_task(tmp_path):
    overlay = yaml.safe_load((PROJECT / "configs/solo.yaml").read_text())
    overlay["inputs"] = {"task": "different"}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(overlay))
    with pytest.raises(ValueError):
        config.load_config(path)


def test_input_binding_rejects_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    root = tmp_path / "run"
    root.mkdir()
    (root / "input-reference.json").write_text(
        json.dumps({"schema": "shared-task.v1", "task_directory": str(link)})
    )
    with pytest.raises(PermissionError):
        task_directory(root)


def test_import_graph_is_independent_of_legacy_workspace(tmp_path):
    code = """import sys, pathlib, importlib
sys.path.insert(0, sys.argv[1])
root=pathlib.Path(sys.argv[1])
for p in (root/'kinetic_agents').rglob('*.py'):
    if '_frozen' in p.parts or 'science_startup' in p.parts: continue
    m='.'.join(p.relative_to(root).with_suffix('').parts)
    if p.stem=='__init__': m=m.removesuffix('.__init__')
    importlib.import_module(m)
assert not {'research_runtime','harness_pair','mechrl','usc_exploration'} & set(sys.modules)
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(PROJECT / "src")], cwd=tmp_path, check=True
    )
