from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kinetic_agents.core.storage import atomic
from kinetic_agents.core.paths import file_sha
from kinetic_agents.evaluation import repair, service
from kinetic_agents.evaluation.scoring import fingerprint, summarize


def write_endpoint(target, manifest, contract, rows, job_id):
    target.mkdir(exist_ok=True)
    atomic(target / "manifest.json", manifest)
    contract = {**contract, "manifest_sha256": fingerprint(manifest)}
    atomic(target / "evaluation_contract.json", contract)
    binding = dict(job_id=job_id, job_name=contract["evaluation_id"],
                   remote_root=contract["remote_root"], contract_sha256=fingerprint(contract))
    atomic(target / "binding.json", binding)
    scores = summarize(manifest, rows)
    result = dict(status="COMPLETE", job_id=job_id, contract_sha256=fingerprint(contract),
                  manifest_sha256=fingerprint(manifest), rows=rows, scores=scores,
                  leaderboard=service.leaderboard(manifest, scores), cpu_receipt={"cpu_seconds": 10})
    atomic(target / "evaluation_result.json", result)
    digest = file_sha(target / "evaluation_result.json")
    atomic(target / "observation.json", dict(scheduler=dict(**{k: binding[k] for k in ("job_id", "job_name")},
        state="COMPLETED", allocated_cpus=64, allocated_core_seconds=64),
        closed=dict(job_id=job_id, result_sha256=digest)))
    atomic(target / "scorecard.json", dict(scores=scores, result_sha256=digest,
        evaluation_allocated_core_seconds=64))


@pytest.fixture
def ended(tmp_path, monkeypatch):
    import cantera as ct
    root = tmp_path / "solo-max"
    (root / "endpoint").mkdir(parents=True)
    (root / "final_artifacts").mkdir()
    parent = root / "endpoint/parent.yaml"
    ct.Solution("h2o2.yaml").write_yaml(str(parent))
    data = yaml.safe_load(parent.read_text())
    data["phases"][0]["kinetics"] = "bulk"
    candidate = root / "endpoint/candidate_1.yaml"
    candidate.write_text(yaml.safe_dump(data))
    sha = file_sha(candidate)
    (root / "final_artifacts" / sha).write_bytes(candidate.read_bytes())
    submission = dict(mechanisms=[dict(sha256=sha)], outcome="submitted")
    atomic(root / "submission.json", submission)
    atomic(root / "result.json", dict(status="COMPLETED", submission=submission))
    atomic(root / "local_cpu.json", dict(status="SETTLED"))
    atomic(root / "evaluator_local_cpu.json", dict(status="SETTLED", cpu_seconds=2.0))
    cases = [dict(case_id=f"case-{i}", fuel_label="H2", operator={"family":
        "ignition_delay" if i < 320 else "laminar_flame_speed"}) for i in range(610)]
    selected = dict(parent=dict(id="parent", path="parent.yaml", sha256=file_sha(parent),
        species_count=10, reaction_count=29, keep=[]))
    manifest = dict(development=cases[:491], recheck=cases[491:], selected=selected,
        source_pins=service.source_pins(), solver_version=ct.__version__,
        numerical_policy={"unchanged": True}, per_case_cpu={"unchanged": True},
        source_submission_sha256=file_sha(root / "submission.json"),
        endpoint_code_sha256="a" * 64, endpoint_pairs=610,
        invalid_candidates=[dict(label="candidate_1", sha256=sha, status="invalid_mechanism")])
    ident = "cfx_test_original"
    contract = dict(evaluation_id=ident, remote_root=str(service.BASE / ident),
        account="independent_evaluation", agent_budget_charge_seconds=0,
        model_calls_allowed=False, scientist_restart_allowed=False,
        evaluation_cap_core_seconds=64*3600, workers=48, python=str(service.BASE / "python"))
    rows = [dict(label="parent", candidate_id="parent", case_id=c["case_id"],
                 status="success", signed_sigma=1) for c in cases]
    write_endpoint(root / "endpoint", manifest, contract, rows, "1000")
    release = tmp_path / "release"
    release.mkdir()
    monkeypatch.setattr("kinetic_agents.release.verify", lambda _: dict(release_sha256="b" * 64,
        files={"kinetic_agents/evaluation/scoring.py": "a" * 64,
               "kinetic_agents/evaluation/service.py": "c" * 64}))
    monkeypatch.setattr(repair, "verify", lambda _: dict(release_sha256="b" * 64,
        files={"kinetic_agents/evaluation/scoring.py": "a" * 64}))
    monkeypatch.setattr(repair.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=1234))
    return root, release


def test_budget_deducts_prior_allocation_and_local_cpu():
    account = repair.budget(230400, 53440, 6.197)
    assert account["repair_allocation_minutes"] == 45
    assert account["maximum_total_charge_seconds"] <= 230400
    assert account["remaining_cap_core_seconds"] < 230400
    for allocated in (230400, float("nan"), -1):
        with pytest.raises((ValueError, PermissionError)):
            repair.budget(230400, allocated, 0)


def test_revision_cannot_overwrite_or_restart(ended):
    root, release = ended
    repair.launch(root, release)
    with pytest.raises(FileExistsError):
        repair.launch(root, release)
    assert not (root / repair.REVISION / "binding.json").exists()  # mocked process, no real sbatch


def test_native_prepare_preserves_bytes_science_and_budget(ended):
    root, release = ended
    before = {p: p.read_bytes() for p in (root / "endpoint").iterdir() if p.is_file()}
    repair.launch(root, release)
    target = root / repair.REVISION
    contract = repair.prepare(target)
    manifest = service.read(target / "manifest.json")
    old = service.read(root / "endpoint/manifest.json")
    assert set(manifest["selected"]) == {"candidate_1"}
    assert manifest["endpoint_pairs"] == 610
    for key in ("development", "recheck", "source_pins", "solver_version", "numerical_policy", "per_case_cpu", "endpoint_code_sha256"):
        assert manifest[key] == old[key]
    assert (target / "candidate_1.yaml").read_bytes() == (root / "endpoint/candidate_1.yaml").read_bytes()
    assert contract["evaluation_cap_core_seconds"] == 230400 - 64 - 2
    assert contract["budget_lineage"]["budget_reset"] is False
    assert contract["scientist_restart_allowed"] is False
    assert all(p.read_bytes() == blob for p, blob in before.items())


def test_merged_rows_keep_same_submission_parent_and_candidate_provenance(ended):
    root, release = ended
    repair.launch(root, release)
    target = root / repair.REVISION
    contract = repair.prepare(target)
    manifest = service.read(target / "manifest.json")
    candidate = manifest["selected"]["candidate_1"]
    rows = [dict(label="candidate_1", candidate_id=candidate["id"], case_id=c["case_id"],
        status="success", signed_sigma=2) for c in manifest["development"] + manifest["recheck"]]
    write_endpoint(target, manifest, contract, rows, "1001")
    card = repair.combine(target)
    assert card["scores"]["parent"]["all610"]["full_pool_mean_abs_sigma"] == 1
    assert card["scores"]["candidate_1"]["all610"]["full_pool_mean_abs_sigma"] == 2
    assert card["evaluation_allocated_core_seconds"] == 128
    assert len(service.read(target / "combined_result.json")["rows"]) == 1220
    assert card["budget"]["combined_charge_upper_bound_seconds"] <= 230400
    assert len(card["row_sources"]) == 2
    assert not card["optimization_feedback_allowed"]
    from kinetic_agents.observability.review import Sources, endpoint_report
    review = endpoint_report(Sources(root))
    assert review["source"] == repair.REVISION + "/combined_scorecard.json"
    assert review["scores"] == card["scores"]
    assert (target / "REPORT.md").is_file()


def test_changed_locked_candidate_and_result_are_rejected(ended):
    root, release = ended
    repair.launch(root, release)
    path = root / "endpoint/candidate_1.yaml"
    path.write_text(path.read_text() + "\n# altered\n")
    with pytest.raises(PermissionError, match="locked candidate"):
        repair.prepare(root / repair.REVISION)
    result = root / "endpoint/evaluation_result.json"
    result.write_text(result.read_text() + " ")
    with pytest.raises(PermissionError, match="identity/hash/accounting"):
        repair.original(root)


def test_original_owner_must_be_settled(ended):
    root, _ = ended
    atomic(root / "evaluator_local_cpu.json", dict(status="ACTIVE", cpu_seconds=2))
    with pytest.raises(PermissionError, match="settled"):
        repair.original(root)


def test_submit_uses_own_contract_not_global_previous_arm(tmp_path, monkeypatch):
    ident = "cfx_intended_bulk_v1"
    remote = service.BASE / ident
    contract = dict(evaluation_id=ident, remote_root=str(remote))
    atomic(tmp_path / "evaluation_contract.json", contract)
    atomic(tmp_path / "deployment.json", dict(status="DEPLOYED_NOT_SUBMITTED"))
    monkeypatch.setattr(service, "verify_inputs", lambda *a: {})
    calls = []
    monkeypatch.setattr(service, "checked", lambda command: (calls.append(command) or "12345"))
    monkeypatch.setattr(service, "ID", "cfx_wrong_other_arm")
    monkeypatch.setattr(service, "REMOTE", service.BASE / "cfx_wrong_other_arm")
    binding = service.submit(tmp_path)
    assert calls == ["sbatch --parsable " + str(remote / "evaluation.sbatch")]
    assert binding["job_name"] == ident
    with pytest.raises(PermissionError, match="already attempted"):
        service.submit(tmp_path)
    assert len(calls) == 1


def test_status_exposes_revision_without_mutating_original(ended, monkeypatch):
    from kinetic_agents import runner
    root, release = ended
    atomic(root / "endpoint/evaluation_state.json", dict(status="COMPLETE", job_id="1000"))
    old = (root / "endpoint/evaluation_state.json").read_bytes()
    repair.launch(root, release)
    monkeypatch.setattr(runner, "run_root", lambda _: root)
    status = runner.status(root.parent)
    assert status["original_evaluation"]["job_id"] == "1000"
    assert status["evaluation"]["status"] == "REPAIR_PREPARING"
    assert not status["evaluation"]["combined_scorecard_available"]
    assert (root / "endpoint/evaluation_state.json").read_bytes() == old


def test_owner_does_not_call_timeout_or_missing_result_success(tmp_path, monkeypatch):
    import kinetic_agents.execution.cpu as cpu
    monkeypatch.setattr(cpu, "run", lambda *a: dict(returncode=0, reason="deadline", cpu_seconds=0))
    assert repair.supervise(tmp_path)["status"] == "FAILED"
