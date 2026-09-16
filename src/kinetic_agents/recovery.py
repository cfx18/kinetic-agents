"""Explicit, host-authorized recovery of the Slurm submission-state race.

Never a fresh experiment or a generic terminal-state reset. Preserve the frozen
original release, contract, clocks, native session and cumulative resource costs.
Only the reviewed infrastructure patch set may differ. No scientific steering.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shutil
import sqlite3
import subprocess
import sys
import time

from kinetic_agents.core.contracts import fingerprint
from kinetic_agents.core.storage import atomic
from kinetic_agents.core.store import Store
from kinetic_agents.execution.jobs import RemoteJobs, LOCAL_OWNER_CEILING
from kinetic_agents.native.subscription import plain
from kinetic_agents.release import build, verify


PATCH_FILES = {
    "kinetic_agents/execution/jobs.py",
    "kinetic_agents/execution/slurm.py",
    "kinetic_agents/execution/finalize.py",
    "kinetic_agents/runner.py",
    "kinetic_agents/cli.py",
    "kinetic_agents/recovery.py",
}
LIFECYCLE_FILES = (
    "local_cpu.json", "owner_finished.json", "evaluator_local_cpu.json",
    "evaluator_owner_finished.json", "result.json", "remote_cleanup_pending.json",
    "coordinator.json", "review_build_status.json",
)


def patch_difference(old, new):
    changed = sorted(n for n in old["files"].keys() | new["files"].keys()
                     if old["files"].get(n) != new["files"].get(n))
    if not changed or set(changed) - PATCH_FILES or set(old["files"]) - set(new["files"]):
        raise PermissionError("recovery must contain only the reviewed infrastructure patch")
    return changed


def execution_release(run, plan):
    run = plain(run)
    pointer = json.loads(plain(run / "recovery-active.json").read_text())
    if pointer.get("directory") not in ("recovery/slurm-race-v1", "recovery/transport-resume-v2"):
        raise PermissionError("unknown recovery revision")
    directory = plain(run / pointer["directory"])
    receipt = json.loads(plain(directory / "receipt.json").read_text())
    if (fingerprint(receipt) != pointer.get("receipt_sha256")
            or receipt["original_source_sha256"] != plan["source_release_sha256"]
            or receipt["contracts_sha256"] != fingerprint(plan["contracts"])):
        raise PermissionError("recovery authorization or original contract changed")
    original, revised = verify(run / "source-release"), verify(directory / "source-release")
    if (original["release_sha256"] != receipt["original_source_sha256"]
            or revised["release_sha256"] != receipt["new_source_sha256"]
            or patch_difference(original, revised) != receipt["changed_files"]):
        raise PermissionError("unapproved recovery source change")
    return directory / "source-release", receipt


def repaired_identity(old, plan, contract, new_sha):
    from kinetic_agents.team.contracts import TeamIdentity

    team = TeamIdentity(
        contract["run_id"], contract["task_sha256"], contract["model"], contract["effort"],
        contract["max_members"], researcher_model=contract.get("researcher_model"),
        researcher_effort=contract.get("researcher_effort"),
    )
    adapter = dict(
        harness=contract["harness"]["name"],
        auth=contract["backend"]["auth"],
        cli=plan["native_version"],
        source_release_sha256=plan["source_release_sha256"],
        team_identity_sha256=fingerprint(team.as_dict()),
    )
    if old["adapter_sha256"] != fingerprint(adapter):
        raise PermissionError("original adapter identity is not the failed frozen runtime")
    return {**old, "adapter_sha256": fingerprint({**adapter, "source_release_sha256": new_sha})}


def native_binding(root, actor, contract):
    """Recover only a unique native index in this actor's isolated CLI home.

    Node 0.28.1 emits resume_hint at clean exit, so an interrupted first turn
    can have durable native history but no host mapping yet. Never inspect
    private message bodies, guess a latest session, or import another home.
    """
    path = root / "native/kimi-native-session.json"
    if path.exists():
        return json.loads(plain(path).read_text()), None
    binding = json.loads(plain(root / "native/session.json").read_text())
    if (binding.get("thread_id"), binding.get("model"), binding.get("effort"), binding.get("harness")) != (
            actor, contract["model"], contract["effort"], "kimi_code_node"):
        raise PermissionError("missing or foreign host actor binding")
    home = plain(root / "native/cli-home")
    index = plain(home / "session_index.jsonl")
    entries = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
    unique = {fingerprint(row): row for row in entries}
    if len(unique) != 1:
        raise PermissionError("native session recovery requires exactly one unambiguous index entry")
    row = next(iter(unique.values()))
    session = row.get("sessionId", "")
    directory = plain(row["sessionDir"])
    if (not re.fullmatch(r"session_[a-zA-Z0-9_-]{1,128}", session)
            or directory.name != session or not directory.is_relative_to(home / "sessions")
            or row["workDir"] != str(root / "work")):
        raise PermissionError("native index escapes this actor/workspace")
    state = json.loads(plain(directory / "state.json").read_text())
    if state.get("workDir") != row["workDir"] or "main" not in state.get("agents", {}):
        raise PermissionError("native metadata disagrees with its own index")
    history = plain(directory / "agents/main/wire.jsonl")
    if not history.is_file() or history.stat().st_size == 0:
        raise PermissionError("native history is absent; cannot create a fresh replacement")
    return dict(actor=actor, session_id=session, model=contract["model"], effort=contract["effort"]), dict(
        source="unique actor-local Node 0.28.1 session index and state metadata",
        index=str(index.relative_to(root)), index_sha256=fingerprint(entries),
        state=str((directory / "state.json").relative_to(root)),
        native_history_bytes=history.stat().st_size,
    )


def check_failed_state(root, store, contract):
    state = store.read_state(("status", "deadline", "contract", "science_reconciliation_required",
                              "thread_id", "research_runtime_identity"))
    if (state.get("status") != "FAILED_REVIEW"
            or state.get("contract") != contract
            or not state.get("thread_id") or state["deadline"] <= time.time()):
        raise PermissionError("only a live-budget failed run can recover")
    if not state.get("science_reconciliation_required"):
        with sqlite3.connect(f"file:{store.path}?mode=ro", uri=True) as db:
            fault = db.execute("SELECT payload FROM events WHERE kind='PRODUCTION_FAULT' ORDER BY seq DESC LIMIT 1").fetchone()
            transport = db.execute("SELECT 1 FROM events WHERE kind='RR_RECOVERY_REQUIRED' LIMIT 1").fetchone()
        error = json.loads(fault[0]).get("error_type") if fault else None
        missing_binding = not (root / "native/kimi-native-session.json").exists()
        if not (error == "OperationalError" or
                (error == "PermissionError" and transport and missing_binding)):
            raise PermissionError("failure is outside the reviewed recovery scope")
        for database in (root / "runtime.sqlite", root / "team.sqlite"):
            if database.exists():
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise PermissionError("database integrity check failed")
        state["recovery_failure_basis"] = dict(error_type=error, database_integrity="ok",
            cause="transport interrupted; native binding missing" if error == "PermissionError"
            else "OperationalError; exact historical SQL cause not recorded")
    if any((root / name).exists() for name in
           ("submission.json", "stop.json", "endpoint/evaluation_result.json")):
        raise PermissionError("submitted/scored research cannot be resumed against its results")
    result = json.loads(plain(root / "result.json").read_text())
    if result.get("status") != "FAILED_REVIEW" or result.get("submission") is not None:
        raise PermissionError("preserve terminal scientific submissions")
    if contract.get("harness", {}).get("name") != "kimi_code_node":
        raise PermissionError("this recovery is qualified only for native Node Kimi")
    native, reconstructed = native_binding(root, state["thread_id"], contract)
    if (native["actor"], native["model"], native["effort"]) != (
            state["thread_id"], contract["model"], contract["effort"]):
        raise PermissionError("native session identity mismatch")
    for name in ("local_cpu.json", "evaluator_local_cpu.json"):
        owner = json.loads(plain(root / name).read_text())
        if owner["status"] != "SETTLED" or Path("/proc", str(owner["pid"])).exists():
            raise PermissionError("previous execution owner is not conclusively stopped")
    return state, native, reconstructed


def activate(store, receipt):
    """One transaction moves costs and identity, without deleting old events."""
    with store.tx() as db:
        if (store._get(db, "status") != "FAILED_REVIEW"
                or store._get(db, "deadline") != receipt["deadline"]
                or store._get(db, "thread_id") != receipt["native_session"]["actor"]
                or store._get(db, "research_runtime_identity") != receipt["old_runtime_identity"]
                or store._get(db, "recovery_id") is not None):
            raise PermissionError("recovery state changed or authorization already consumed")
        prior = store._get(db, "prior_attempt_cpu_seconds", 0)
        store._put(db, "prior_attempt_cpu_seconds", prior + receipt["prior_local_cpu_seconds"]
                   + receipt["recovery_preparation_cpu_seconds"])
        store._put(db, "recovery_prior_local_cpu_seconds", receipt["prior_local_cpu_seconds"]
                   + receipt["recovery_preparation_cpu_seconds"])
        store._put(db, "recovery_prior_evaluator_cpu_seconds", receipt["prior_evaluator_cpu_seconds"])
        store._put(db, "research_runtime_identity", receipt["new_runtime_identity"])
        store._put(db, "recovery_id", receipt["revision"])
        store._put(db, "science_reconciliation_required", False)
        store._put(db, "status", "RECOVERING")
        store._event(db, "RR_AUTHORIZED_INFRASTRUCTURE_RECOVERY", receipt)


def host_cpu():
    rows = (resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN))
    return sum(r.ru_utime + r.ru_stime for r in rows)


def recover(run):
    """Explicit CLI action: reconcile, freeze a revision, resume original actor."""
    from kinetic_agents import runner
    from kinetic_agents.harnesses.sandbox import qualify
    from kinetic_agents.core.inputs import task_directory
    from kinetic_agents.connections import load_credentials

    before = host_cpu()
    run = plain(run)
    if (run / "recovery-active.json").exists():
        raise PermissionError("recovery already attempted; never launch a duplicate owner")
    root, contract, plan = runner.load(run)
    with (root / "launch.lock").open("a") as launch, (root / "coordinator.lock").open("a") as owner:
        fcntl.flock(launch, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(root / "runtime.sqlite")
        state, native, reconstructed = check_failed_state(root, store, contract)
        # Validate the complete existing team/adapter binding before preparing
        # any revision or touching remote accounting.
        repaired_identity(state["research_runtime_identity"], plan, contract,
                          plan["source_release_sha256"])
        revision = "slurm-race-v1" if state.get("science_reconciliation_required") else "transport-resume-v2"
        directory = run / "recovery" / revision
        directory.mkdir(parents=True, mode=0o700)  # refuse overwriting any recovery attempt
        previous = directory / "previous"
        previous.mkdir()
        with sqlite3.connect(store.path) as src, sqlite3.connect(previous / "runtime.sqlite") as dst:
            src.backup(dst)
        for name in LIFECYCLE_FILES:
            path = root / name
            if path.exists():
                shutil.copy2(plain(path), previous / name)
        original = verify(run / "source-release")
        revised = build(directory / "source-release")
        changed = patch_difference(original, revised)
        load_credentials(contract["backend"])  # Never expose the values in the receipt.
        env = dict(os.environ)
        if "network" in contract:
            from kinetic_agents.network import preflight

            env, network_receipt = preflight(contract["network"], contract["backend"])
            atomic(directory / "network_preflight.json", network_receipt)
        qualify(root, task_directory(root), contract["harness"])
        jobs = RemoteJobs(root, store)
        jobs.step()  # Terminal account cannot submit; settle only already-owned jobs.
        if any(r["status"] not in ("SETTLED", "REJECTED") for r in jobs.rows()):
            raise PermissionError("old remote effects unresolved; refusing to resume or duplicate")
        local = json.loads((previous / "local_cpu.json").read_text())["cpu_seconds"]
        evaluation = json.loads((previous / "evaluator_local_cpu.json").read_text())["cpu_seconds"]
        preparation = max(0.0, host_cpu() - before)
        if (local + preparation >= LOCAL_OWNER_CEILING or evaluation >= 3600
                or jobs.budget()["remote_admission_remaining_seconds"] <= local + preparation):
            raise PermissionError("original cumulative CPU allowance cannot support recovery")
        receipt = dict(
            revision=revision, at=time.time(),
            authorization="User explicitly requested resume of stopped Kimi runs",
            failure_basis=state.get("recovery_failure_basis", {"science_reconciliation_required": True}),
            original_source_sha256=original["release_sha256"],
            new_source_sha256=revised["release_sha256"], changed_files=changed,
            contracts_sha256=fingerprint(plan["contracts"]),
            deadline=state["deadline"], native_session=native,
            native_binding_reconstruction=reconstructed,
            old_runtime_identity=state["research_runtime_identity"],
            new_runtime_identity=repaired_identity(state["research_runtime_identity"], plan,
                                                  contract, revised["release_sha256"]),
            prior_local_cpu_seconds=local, prior_evaluator_cpu_seconds=evaluation,
            recovery_preparation_cpu_seconds=preparation,
            remote_jobs=[{k: r.get(k) for k in ("id", "job_id", "status", "charged_seconds")}
                         for r in jobs.rows()],
            preserves="original task/model/native history/evidence/budgets/deadline; no scientific hint",
        )
        atomic(directory / "receipt.json", receipt)
        atomic(run / "recovery-active.json", dict(directory=f"recovery/{revision}",
                                                  receipt_sha256=fingerprint(receipt)))
        if reconstructed:
            atomic(root / "native/kimi-native-session.json", native)
        # Move lifecycle views only, after immutable copies exist. Research data,
        # native session, API logs and original database/events stay in place.
        moved = directory / "displaced"
        moved.mkdir()
        for name in LIFECYCLE_FILES:
            path = root / name
            if path.exists():
                path.rename(moved / name)
        activate(store, receipt)
        atomic(root / "remote_status.json", dict(jobs=receipt["remote_jobs"],
               resources={"cpu_remaining_seconds": jobs.budget()["remote_admission_remaining_seconds"]},
               resource_stopped=False, stop=None))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        # Release coordinator lock before its newly spawned owner takes it.
        fcntl.flock(owner, fcntl.LOCK_UN)
        with (root / "coordinator.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "kinetic_agents.runner", "supervise", "--run", str(run)],
                cwd=directory / "source-release", env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True,
            )
        value = dict(pid=process.pid, run=str(run), recovery=receipt["revision"],
                     deadline=receipt["deadline"], model=contract["model"], effort=contract["effort"],
                     observer_required=False, native_session_id=native["session_id"],
                     note="Recovery owner launched; verify a real resumed model response next")
        atomic(root / "coordinator.json", value)
        runner.checkpoint(run, "infrastructure_recovery_launched")
        return value
