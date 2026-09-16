import json
import threading
import time
from pathlib import Path

import pytest

from kinetic_agents.core.state import EnvironmentState
from kinetic_agents.core.store import Store
from kinetic_agents.execution import jobs, slurm
from kinetic_agents.recovery import activate, patch_difference, repaired_identity
from kinetic_agents.core.contracts import fingerprint


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "team-max"
    (root / "work").mkdir(parents=True)
    (root / "work/main.py").write_text("print('synthetic only')")
    base = "/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs"
    store = Store(root / "runtime.sqlite")
    store.initialize(dict(api_usd=0, cpu_seconds=512*3600, wall_seconds=86400,
                          run_id="cfx_fixture", arm="team-max", deployment=dict(
                              search_root=base+"/search", container_image=base+"/runtime/image.sif",
                              evaluation_python=base+"/runtime/python")))
    (tmp_path / "qualification").mkdir()
    (tmp_path / "qualification/accepted.json").write_text(json.dumps(
        dict(status="PASS", image_sha256="a"*64)))
    remote = jobs.RemoteJobs(root, store, transport=False)
    remote.submit("pi", dict(argv=["python", "main.py"], inputs=["main.py"],
                             outputs=["out.txt"], minutes=5), "fixture")
    return remote


def environment_state(remote):
    return EnvironmentState.from_snapshot(dict(
        jobs=[dict(status="FAILED_UNCERTAIN" if r["status"] == "SUBMIT_UNCERTAIN"
                   else r["status"]) for r in remote.rows()],
        resources=dict(cpu_remaining_seconds=1000), stop=None, resource_stopped=False))


def test_live_submit_observed_concurrently_does_not_kill_run(remote, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    failures = []

    def checked(command):
        if command.startswith("sbatch"):
            entered.set()
            assert release.wait(5)
            return "12345\n"
        return ""

    monkeypatch.setattr(jobs, "checked", checked)
    monkeypatch.setattr(jobs, "transfer", lambda *a: None)

    def dispatch():
        try:
            remote.step()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=dispatch)
    thread.start()
    try:
        assert entered.wait(5)
        for _ in range(12):
            assert remote.rows()[0]["status"] == "SUBMITTING"
            assert environment_state(remote).unresolved_jobs == 0
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert remote.rows()[0]["job_id"] == "12345"
    assert remote.rows()[0]["status"] == "SUBMITTED"


@pytest.mark.parametrize("outcome", ["timeout", "unparseable"])
def test_genuinely_unknown_submit_remains_fail_closed_never_reissued(remote, monkeypatch, outcome):
    calls = []

    def checked(command):
        if command.startswith("sbatch"):
            calls.append(command)
            if outcome == "timeout":
                raise TimeoutError("synthetic unknown acceptance")
            return "accepted without an id"
        return ""

    monkeypatch.setattr(jobs, "checked", checked)
    monkeypatch.setattr(jobs, "transfer", lambda *a: None)
    reserved = remote.budget()["remote_charged_or_reserved_seconds"]
    with pytest.raises((TimeoutError, ConnectionError)):
        remote.step()
    assert environment_state(remote).unresolved_jobs == 1
    for _ in range(3):
        remote.step()
    assert len(calls) == 1
    assert remote.budget()["remote_charged_or_reserved_seconds"] == reserved


def test_crashed_inflight_intent_is_not_resubmitted(remote, monkeypatch):
    row = remote.rows()[0]
    row["status"] = "SUBMITTING"
    remote._put(row)
    monkeypatch.setattr(jobs, "checked", lambda *a: pytest.fail("must not submit"))
    remote.step()
    assert remote.rows()[0]["status"] == "SUBMIT_UNCERTAIN"
    assert environment_state(remote).unresolved_jobs == 1


@pytest.mark.parametrize("raw,cpus,seconds", [("CANCELLED by 24101", 64, 3),
                                            ("CANCELLED", 0, 0)])
def test_cancel_state_is_normalized_and_cost_preserved(monkeypatch, raw, cpus, seconds):
    monkeypatch.setattr(slurm, "checked", lambda cmd:
                        f"123|cfx_fixture|{raw}|{cpus}|{seconds}|{cpus*seconds}\n")
    row = slurm.scheduler(dict(job_id="123", job_name="cfx_fixture"))
    assert row["state"] == "CANCELLED" and row["raw_state"] == raw
    assert row["allocated_core_seconds"] == cpus*seconds


def test_cancel_does_not_weaken_job_identity(monkeypatch):
    monkeypatch.setattr(slurm, "checked", lambda cmd: "123|someone_else|CANCELLED by 2|64|3|192\n")
    with pytest.raises(PermissionError):
        slurm.scheduler(dict(job_id="123", job_name="cfx_fixture"))


def test_cancelled_job_collects_once_and_no_new_sbatch(remote, monkeypatch):
    row = remote.rows()[0]
    row.update(status="SUBMITTED", job_id="123", remote="synthetic")
    remote._put(row)
    monkeypatch.setattr(jobs, "scheduler", lambda row: dict(state="CANCELLED", allocated_core_seconds=192))
    monkeypatch.setattr(jobs, "checked", lambda cmd: "MISSING")
    remote.step()
    assert remote.rows()[0]["status"] == "SETTLED"
    assert remote.rows()[0]["charged_seconds"] == 192
    assert remote.rows()[0]["output_status"] == "UNAVAILABLE"


def test_recovery_preserves_deadline_history_costs_and_is_one_shot(tmp_path):
    store = Store(tmp_path / "runtime.sqlite")
    contract = dict(api_usd=0, wall_seconds=86400)
    store.initialize(contract)
    old = dict(adapter_sha256="old", policy_sha256="unchanged")
    store.update("SYNTHETIC_FAILURE", status="FAILED_REVIEW", thread_id="pi",
                 research_runtime_identity=old, failures=2, prior_attempt_cpu_seconds=10)
    deadline = store.get("deadline")
    receipt = dict(revision="fixture", deadline=deadline, native_session=dict(actor="pi"),
                   old_runtime_identity=old, new_runtime_identity={**old, "adapter_sha256":"new"},
                   prior_local_cpu_seconds=123, recovery_preparation_cpu_seconds=1,
                   prior_evaluator_cpu_seconds=2)
    activate(store, receipt)
    assert store.get("deadline") == deadline
    assert store.get("contract") == contract and store.get("thread_id") == "pi"
    assert store.get("failures") == 2 and store.get("prior_attempt_cpu_seconds") == 134
    assert store.get("recovery_prior_evaluator_cpu_seconds") == 2
    assert store.get("status") == "RECOVERING"
    with pytest.raises(PermissionError):
        activate(store, receipt)


def test_revision_refuses_scientific_edits_and_keeps_policy_identity():
    from kinetic_agents.team.contracts import TeamIdentity
    with pytest.raises(PermissionError):
        patch_difference(dict(files={"science.py":"a"}), dict(files={"science.py":"b"}))
    assert patch_difference(dict(files={"kinetic_agents/execution/jobs.py":"a"}),
                            dict(files={"kinetic_agents/execution/jobs.py":"b"}))
    contract = dict(run_id="fixture", task_sha256="a"*64, model="synthetic", effort="max",
                    max_members=3, harness=dict(name="kimi_code_node"), backend=dict(auth="api"))
    team = TeamIdentity("fixture", "a"*64, "synthetic", "max", 3)
    adapter = dict(harness="kimi_code_node", auth="api", cli="fixture", source_release_sha256="old",
                   team_identity_sha256=fingerprint(team.as_dict()))
    old = dict(adapter_sha256=fingerprint(adapter), policy_sha256="p", tools_sha256="t")
    new = repaired_identity(old, dict(native_version="fixture", source_release_sha256="old"),
                            contract, "new")
    assert new["adapter_sha256"] != old["adapter_sha256"]
    assert new["policy_sha256"] == old["policy_sha256"] and new["tools_sha256"] == old["tools_sha256"]


@pytest.mark.parametrize("database_failure", [False, True])
def test_full_recovery_archives_failure_and_resumes_same_capsule_without_submission(tmp_path, monkeypatch, database_failure):
    from types import SimpleNamespace
    from kinetic_agents import recovery, runner
    from kinetic_agents.harnesses import sandbox
    from kinetic_agents import connections
    from kinetic_agents.team.contracts import TeamIdentity

    run = tmp_path / "run"
    root = run / "team-max"
    (root / "native").mkdir(parents=True)
    (root / "work").mkdir()
    (root / "work/evidence.txt").write_text("own previous evidence")
    (root / "native/api_requests.jsonl").write_text('{"status":"STARTED","id":"unknown-old"}\n')
    native = dict(actor="pi", session_id="native-original", model="synthetic", effort="max")
    (root / "native/kimi-native-session.json").write_text(json.dumps(native))
    contract = dict(api_usd=0, wall_seconds=86400, cpu_seconds=512*3600, arm="team-max",
                    run_id="fixture", task_sha256="a"*64, max_members=3,
                    model="synthetic", effort="max", harness=dict(name="kimi_code_node"),
                    backend=dict(auth="api"))
    plan = dict(contracts={"team-max":contract}, native_version="fixture", source_release_sha256="old")
    team = TeamIdentity("fixture", "a"*64, "synthetic", "max", 3)
    adapter = dict(harness="kimi_code_node", auth="api", cli="fixture", source_release_sha256="old",
                   team_identity_sha256=fingerprint(team.as_dict()))
    identity = dict(adapter_sha256=fingerprint(adapter), policy_sha256="unchanged")
    store = Store(root / "runtime.sqlite")
    store.initialize(contract)
    deadline = store.get("deadline")
    store.update("SYNTHETIC_FAILURE", status="FAILED_REVIEW", science_reconciliation_required=not database_failure,
                 thread_id="pi", research_runtime_identity=identity)
    if database_failure:
        with store.tx() as db:
            store._event(db, "PRODUCTION_FAULT", {"error_type": "OperationalError"})
    for name, value in {
        "result.json":dict(status="FAILED_REVIEW", submission=None),
        "local_cpu.json":dict(status="SETTLED", cpu_seconds=123, pid=2147483647),
        "evaluator_local_cpu.json":dict(status="SETTLED", cpu_seconds=1, pid=2147483647),
    }.items():
        (root / name).write_text(json.dumps(value))
    old = dict(release_sha256="old", files={"kinetic_agents/execution/jobs.py":"a"})
    new = dict(release_sha256="new", files={"kinetic_agents/execution/jobs.py":"b"})
    monkeypatch.setattr(runner, "load", lambda path: (root, contract, plan))
    monkeypatch.setattr(runner, "checkpoint", lambda *a: None)
    monkeypatch.setattr(recovery, "verify", lambda path: old)
    def build(path):
        path.mkdir()
        return new
    monkeypatch.setattr(recovery, "build", build)
    monkeypatch.setattr(connections, "load_credentials", lambda *a: ("fixture", "not-exported"))
    monkeypatch.setattr(sandbox, "qualify", lambda *a: None)
    launched = []
    def popen(command, **kwargs):
        launched.append((command, kwargs))
        return SimpleNamespace(pid=123456)
    monkeypatch.setattr(recovery.subprocess, "Popen", popen)
    result = recovery.recover(run)
    assert result["native_session_id"] == "native-original"
    assert result["deadline"] == deadline and store.get("deadline") == deadline
    assert store.get("status") == "RECOVERING" and store.get("contract") == contract
    assert store.get("prior_attempt_cpu_seconds") >= 123
    assert (root / "work/evidence.txt").read_text() == "own previous evidence"
    assert "unknown-old" in (root / "native/api_requests.jsonl").read_text()
    assert not (root / "result.json").exists() and not (root / "local_cpu.json").exists()
    revision = "transport-resume-v2" if database_failure else "slurm-race-v1"
    archived = run / "recovery" / revision / "previous/result.json"
    assert json.loads(archived.read_text())["status"] == "FAILED_REVIEW"
    assert len(launched) == 1
    assert launched[0][1]["cwd"] == run / "recovery" / revision / "source-release"
    assert launched[0][1]["start_new_session"] is True
    with pytest.raises(PermissionError, match="already attempted"):
        recovery.recover(run)


def test_missing_resume_hint_uses_only_unique_owned_native_index(tmp_path):
    from kinetic_agents.recovery import native_binding

    root = tmp_path / "team-max"
    home = root / "native/cli-home"
    session = home / "sessions/wd_fixture/session_original"
    (session / "agents/main").mkdir(parents=True)
    contract = dict(model="synthetic", effort="max")
    (root / "native/session.json").write_text(json.dumps(
        dict(thread_id="pi", harness="kimi_code_node", **contract)))
    row = dict(sessionId="session_original", sessionDir=str(session), workDir=str(root/"work"))
    index = home / "session_index.jsonl"
    index.write_text(json.dumps(row)+"\n")
    state = session / "state.json"
    state.write_text(json.dumps(dict(workDir=row["workDir"], agents={"main":{}})))
    (session / "agents/main/wire.jsonl").write_text("synthetic opaque bytes; not read by recovery")
    native, provenance = native_binding(root, "pi", contract)
    assert native["session_id"] == "session_original" and provenance["native_history_bytes"] > 0
    with pytest.raises(PermissionError, match="foreign host"):
        native_binding(root, "other", contract)
    index.write_text(json.dumps(row)+"\n"+json.dumps({**row,"sessionId":"session_other"})+"\n")
    with pytest.raises(PermissionError, match="unambiguous"):
        native_binding(root, "pi", contract)
    index.write_text(json.dumps({**row, "sessionDir":str(tmp_path/"foreign/session_original")})+"\n")
    with pytest.raises(PermissionError, match="escapes"):
        native_binding(root, "pi", contract)
