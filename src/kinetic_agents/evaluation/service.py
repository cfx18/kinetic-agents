"""Independent submission -> Slurm evaluation -> scorecard.

No Agent database, API credentials or research checkpoint is read or debited.
Evaluation has its own bounded cost ledger. Monitoring never drives the worker.
"""

import argparse
from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import wait
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tarfile
import time

from kinetic_agents.core.storage import atomic
from kinetic_agents.core.storage import locked
from kinetic_agents.evaluation.pins import source_pins
from kinetic_agents.evaluation.scoring import fingerprint
from kinetic_agents.evaluation.scoring import summarize
from kinetic_agents.evaluation.scoring import validate_pool
from kinetic_agents.core.paths import plain
from kinetic_agents.core.paths import file_sha
from kinetic_agents.execution.slurm import BASE
from kinetic_agents.execution.slurm import checked
from kinetic_agents.execution.slurm import transfer
from kinetic_agents.execution.slurm import script
from kinetic_agents.execution.slurm import scheduler
from kinetic_agents.execution.slurm import remote_path

ID = "cfx_unbound_endpoint"
REMOTE = BASE / ID
PYTHON = None  # Bound by the submitted run's common deployment contract.
MINUTES = 59
WORKERS = 48
AUTHORITY = (
    "用户：这个不占任务核时啊，相当于这是评测系统，不是agent任务的部分，"
    "当agent觉得可以结束任务了，他就提交到评测系统，评测系统直接返回分数排名就行了"
)


def read(path):
    return json.loads(plain(path).read_text())


def verify_inputs(root, contract):
    root = plain(root)
    manifest = read(root / "manifest.json")
    if fingerprint(manifest) != contract["manifest_sha256"]:
        raise PermissionError("evaluation input manifest changed")
    validate_pool(manifest)
    if (
        contract.get("account") != "independent_evaluation"
        or contract.get("agent_budget_charge_seconds") != 0
        or contract.get("model_calls_allowed") is not False
        or contract.get("scientist_restart_allowed") is not False
    ):
        raise PermissionError("independent evaluator boundary changed")
    for candidate in manifest["selected"].values():
        name = candidate["path"]
        if Path(name).name != name or file_sha(root / name) != candidate["sha256"]:
            raise PermissionError("committed mechanism changed or foreign")
    if source_pins() != manifest["source_pins"]:
        raise PermissionError("scientific implementation differs from benchmark")
    return manifest


def register(root, release, *, cap_seconds=64 * 3600, local_overhead_seconds=3600.0,
             budget_lineage=None):
    """Use the already locked submission; change only evaluator ownership."""
    from kinetic_agents.release import verify

    if PYTHON is None:
        raise PermissionError("bind evaluation Python from this run's deployment contract first")
    if (not math.isfinite(cap_seconds) or not 0 < cap_seconds <= 64 * 3600
            or not math.isfinite(local_overhead_seconds)
            or not 0 < local_overhead_seconds <= 3600):
        raise ValueError("bounded evaluator account required; revisions cannot reset the original cap")
    root, release = plain(root), plain(release)
    if (root / "evaluation_contract.json").exists():
        raise PermissionError("evaluation registration is immutable")
    manifest = read(root / "manifest.json")
    if read(root / "commitment.json")["manifest_sha256"] != fingerprint(manifest):
        raise PermissionError("precommitted task changed")
    package = verify(release)
    if (
        package["files"].get("kinetic_agents/evaluation/scoring.py")
        != manifest["endpoint_code_sha256"]
    ):
        raise PermissionError("precommitted endpoint code changed")
    contract = dict(
        schema="independent-endpoint.v1",
        evaluation_id=ID,
        authority=AUTHORITY,
        account="independent_evaluation",
        agent_budget_charge_seconds=0.0,
        model_calls_allowed=False,
        model_usd=0.0,
        scientist_restart_allowed=False,
        return_terminal_scorecard=True,
        optimization_feedback_allowed=False,
        manifest_sha256=fingerprint(manifest),
        release_sha256=package["release_sha256"],
        evaluator_source_sha256=package["files"]["kinetic_agents/evaluation/service.py"],
        local_release=str(release),
        remote_root=str(REMOTE),
        python=str(PYTHON),
        workers=WORKERS,
        allocation_minutes=MINUTES,
        allocation_cores=64,
        evaluation_cap_core_seconds=cap_seconds,
        local_overhead_cap_seconds=local_overhead_seconds,
        limit_policy="operational ceiling chosen within the previously proposed evaluator envelope; not Agent budget",
        registered_at=time.time(),
    )
    if budget_lineage is not None:
        contract["budget_lineage"] = budget_lineage
    if (
        MINUTES * 60 * 64 + contract["local_overhead_cap_seconds"]
        > contract["evaluation_cap_core_seconds"]
    ):
        raise ValueError("evaluator allocation exceeds its own envelope")
    verify_inputs(root, contract)
    atomic(root / "evaluation_contract.json", contract)
    atomic(
        root / "evaluation_state.json",
        dict(
            status="REGISTERED",
            evaluation_id=ID,
            account="independent_evaluation",
            agent_budget_charged=False,
            model_calls=0,
        ),
    )
    return contract


def deploy(root):
    from kinetic_agents.release import verify

    root = plain(root)
    contract = read(root / "evaluation_contract.json")
    verify_inputs(root, contract)
    if (root / "deployment.json").exists() or (root / "submission_intent.json").exists():
        raise PermissionError("existing deployment must not be repeated")
    release = plain(contract["local_release"])
    package = verify(release)
    if package["release_sha256"] != contract["release_sha256"]:
        raise PermissionError("release changed")
    with tarfile.open(root / "evaluator_release.tar", "x") as archive:
        for name in [*package["files"], "release-manifest.json"]:
            archive.add(release / name, arcname=name, recursive=False)
    remote = remote_path(contract["remote_root"])
    science = remote / "release"
    python = remote_path(contract["python"])
    batch = script(
        remote, science, python, contract["evaluation_id"], contract["allocation_minutes"]
    )
    batch = batch.replace("set -euo pipefail", "#SBATCH --signal=B:TERM@30\nset -euo pipefail")
    batch = batch.replace(
        str(python) + " -B -m kinetic_agents.execution.slurm_worker --spool " + str(remote),
        "export CFX_SCIENCE_LOG_DIR="
        + str(remote / "runtime_logs")
        + "\n"
        + "export PYTHONPATH="
        + str(science / "kinetic_agents/evaluation/science_startup")
        + ":"
        + str(science)
        + "\n"
        + "exec "
        + str(python)
        + " -B -m kinetic_agents.evaluation.service execute --root "
        + str(remote)
        + " --contract-sha "
        + fingerprint(contract),
    )
    if "slurm_worker --spool" in batch:
        raise ValueError("batch entrypoint replacement failed")
    # Fixed host-owned script, never model-generated shell.
    with (root / "evaluation.sbatch").open("x") as stream:
        stream.write(batch)
    checked(
        "mkdir -m 700 "
        + str(remote)
        + " && mkdir -m 700 "
        + str(science)
        + " "
        + str(remote / "runtime_logs")
    )
    manifest = read(root / "manifest.json")
    for name in [
        "manifest.json",
        "evaluation_contract.json",
        "evaluation.sbatch",
        "evaluator_release.tar",
        *[v["path"] for v in manifest["selected"].values()],
    ]:
        transfer(root / name, remote / name)
    checked("tar -xf " + str(remote / "evaluator_release.tar") + " -C " + str(science))
    atomic(
        root / "deployment.json",
        dict(
            status="DEPLOYED_NOT_SUBMITTED",
            remote=str(remote),
            release_sha256=contract["release_sha256"],
            at=time.time(),
        ),
    )
    return {"status": "DEPLOYED_NOT_SUBMITTED"}


def submit(root):
    root = plain(root)
    contract = read(root / "evaluation_contract.json")
    # Bind submission to its immutable contract, never a previous caller's
    # module globals (especially when preparing two independent revisions).
    ident = contract["evaluation_id"]
    remote = remote_path(contract["remote_root"])
    if not re.fullmatch(r"cfx_[a-zA-Z0-9_]+", ident) or remote.name != ident:
        raise PermissionError("evaluator job identity mismatch")
    verify_inputs(root, contract)
    with locked(root / "submit.lock"):
        if (root / "submission_intent.json").exists():
            raise PermissionError(
                "submission already attempted; reconcile exact job, never blind resubmit"
            )
        if read(root / "deployment.json")["status"] != "DEPLOYED_NOT_SUBMITTED":
            raise PermissionError("deployment required")
        atomic(
            root / "submission_intent.json",
            {
                "job_name": ident,
                "at": time.time(),
                "contract_sha256": fingerprint(contract),
                "status": "SUBMITTING",
            },
        )
        response = checked("sbatch --parsable " + str(remote / "evaluation.sbatch")).strip()
        job_id = response.split(";")[0]
        if not re.fullmatch("[0-9]+", job_id):
            raise ConnectionError("ambiguous sbatch response; reconcile intent, do not retry")
        binding = {
            "job_id": job_id,
            "job_name": ident,
            "host": "sca2070",
            "remote_root": str(remote),
            "contract_sha256": fingerprint(contract),
        }
        atomic(root / "binding.json", binding)
        atomic(
            root / "evaluation_state.json",
            {
                "status": "SUBMITTED",
                **binding,
                "account": "independent_evaluation",
                "agent_budget_charged": False,
                "model_calls": 0,
            },
        )
        return binding


def work_items(manifest):
    for split in ("development", "recheck"):
        for case in manifest[split]:
            for label, candidate in manifest["selected"].items():
                yield label, candidate, case, split


def row_name(label, case_id):
    return fingerprint([label, case_id]) + ".json"


def solve_one(root, label, candidate, case, split, policy, grant):
    """One process per active slot keeps LocalBackend's wait accounting isolated."""
    from kinetic_agents.evaluation._frozen.mechrl.usc_backend import LocalBackend

    root = Path(root)
    name = row_name(label, case["case_id"])
    atomic(
        root / "attempts" / name,
        {
            "label": label,
            "candidate_id": candidate["id"],
            "case_id": case["case_id"],
            "pid": os.getpid(),
            "started": time.time(),
        },
    )
    missing = sorted(set(case["initial_state"]["mole_fractions"]) - set(candidate["keep"]))
    if missing:
        row = dict(
            candidate_id=candidate["id"],
            case_id=case["case_id"],
            status="input_incompatible",
            missing_species=missing,
            actual_cpu_seconds=0.0,
            logical_cpu_seconds=0.0,
            solver_invocations=0,
        )
    else:
        bound = {**candidate, "path": str(root / candidate["path"])}
        row = LocalBackend(
            [case], root / "cache" / label, python=sys.executable, numerical_policy=policy
        ).execute(bound, case["case_id"], "query", [], grant)
    if row["candidate_id"] != candidate["id"] or row["case_id"] != case["case_id"]:
        raise ValueError("scientific response identity mismatch")
    row.update(label=label, split=split)
    atomic(root / "receipts" / name, row)
    return {
        "label": label,
        "status": row["status"],
        "case_id": case["case_id"],
        "actual_cpu_seconds": row.get("actual_cpu_seconds", 0.0),
    }


def compute(root):
    root = plain(root)
    contract = read(root / "evaluation_contract.json")
    manifest = verify_inputs(root, contract)
    started = read(root / "started.json")
    if (
        os.environ.get("SLURM_JOB_ID") != started["job_id"]
        or os.environ.get("SLURM_JOB_NAME") != contract["evaluation_id"]
    ):
        raise PermissionError("owned evaluator allocation required")
    items = iter(work_items(manifest))
    pending = {}
    total = 0
    cpu = 0.0
    counts = Counter()
    shutdown = []
    last_write = 0.0
    last_completion = time.time()
    signal.signal(signal.SIGTERM, lambda n, _f: shutdown.append(n))
    signal.signal(signal.SIGINT, lambda n, _f: shutdown.append(n))
    pool = ProcessPoolExecutor(max_workers=contract["workers"])
    try:
        exhausted = False
        while pending or not exhausted:
            stopping = bool(shutdown) or time.time() >= started["compute_deadline"] - 5
            while not exhausted and not stopping and len(pending) < contract["workers"]:
                item = next(items, None)
                if item is None:
                    exhausted = True
                    break
                label, candidate, case, split = item
                future = pool.submit(
                    solve_one,
                    str(root),
                    label,
                    candidate,
                    case,
                    split,
                    manifest["numerical_policy"],
                    manifest["per_case_cpu"][case["operator"]["family"]],
                )
                pending[future] = item
            if stopping:
                break  # Dedicated CPU owner handles the bounded process-tree reap.
            if pending:
                done, _ = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in done:
                    label, candidate, case, split = pending.pop(future)
                    try:
                        row = future.result()
                        cpu += row["actual_cpu_seconds"]
                        status = row["status"]
                    except Exception as exc:
                        status = "evaluation_worker_error"
                        atomic(
                            root / "receipts" / row_name(label, case["case_id"]),
                            {
                                "label": label,
                                "candidate_id": candidate["id"],
                                "case_id": case["case_id"],
                                "split": split,
                                "status": status,
                                "error_type": type(exc).__name__,
                                "error": str(exc)[:1000],
                                "actual_cpu_seconds": None,
                                "cost_record": "whole-allocation ledger; individual receipt unavailable",
                            },
                        )
                    total += 1
                    counts[status] += 1
                    last_completion = time.time()
            if time.time() - last_write >= 5.0 or (exhausted and not pending):
                atomic(
                    root / "progress.json",
                    dict(
                        status="RUNNING" if pending or not exhausted else "CASES_FINISHED",
                        recorded=total,
                        expected=manifest["endpoint_pairs"],
                        running=len(pending),
                        statuses=dict(counts),
                        settled_case_cpu_seconds=cpu,
                        last_completion=last_completion,
                        at=time.time(),
                        job_id=started["job_id"],
                    ),
                )
                last_write = time.time()
        if not pending and exhausted:
            atomic(root / "compute_finished.json", {"at": time.time(), "records": total})
    finally:
        pool.shutdown(wait=not pending, cancel_futures=True)


def leaderboard(manifest, scores):
    """Accuracy ranks and Pareto sets are separate, without a new scalar weight."""
    ranked = []
    for label, candidate in manifest["selected"].items():
        score = scores[label]["all610"]
        error = score["full_pool_mean_abs_sigma"]
        ranked.append(
            dict(
                label=label,
                species=candidate["species_count"],
                reactions=candidate["reaction_count"],
                reference=label == "parent",
                mean_abs_sigma=error,
                coverage=score["coverage"],
                accuracy_rank=None,
                on_pareto_front=None,
                eligibility="full_pool" if error is not None else "incomplete_coverage",
            )
        )
    eligible = [x for x in ranked if x["mean_abs_sigma"] is not None]
    for row in eligible:
        row["accuracy_rank"] = 1 + sum(
            other["mean_abs_sigma"] < row["mean_abs_sigma"] for other in eligible
        )

        def dominates(other):
            a = (other["species"], other["reactions"], other["mean_abs_sigma"])
            b = (row["species"], row["reactions"], row["mean_abs_sigma"])
            return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))

        row["on_pareto_front"] = not any(dominates(other) for other in eligible)
    return {
        "rank_scope": "this fixed submission plus parent, not global leaderboard",
        "rank_metric": "full610 mean absolute sigma only; complexity reported separately",
        "rows": sorted(
            ranked, key=lambda r: (r["accuracy_rank"] is None, r["accuracy_rank"] or 0, r["label"])
        ),
    }


def collect_rows(root, manifest):
    rows = []
    expected_names = set()
    for label, candidate, case, split in work_items(manifest):
        name = row_name(label, case["case_id"])
        expected_names.add(name)
        path = root / "receipts" / name
        if path.exists():
            row = read(path)
            if (
                row.get("label"),
                row.get("candidate_id"),
                row.get("case_id"),
                row.get("split"),
            ) != (label, candidate["id"], case["case_id"], split):
                raise PermissionError("foreign evaluation receipt")
        else:
            status = (
                "evaluation_interrupted" if (root / "attempts" / name).exists() else "not_evaluated"
            )
            row = dict(
                label=label,
                candidate_id=candidate["id"],
                case_id=case["case_id"],
                split=split,
                status=status,
                actual_cpu_seconds=None,
                cost_record="whole-allocation ledger",
            )
        rows.append(row)
    if {p.name for p in (root / "receipts").glob("*.json")} - expected_names:
        raise PermissionError("unexpected evaluation receipt")
    return rows


def finish(root, contract, errors):
    """Independent finalization, even when the compute supervisor failed."""
    manifest = verify_inputs(root, contract)
    rows = collect_rows(root, manifest)
    scores = summarize(manifest, rows)
    cpu = read(root / "cpu.json") if (root / "cpu.json").exists() else {"status": "UNKNOWN"}
    missing = sum(r["status"] in ("not_evaluated", "evaluation_interrupted") for r in rows)
    status = (
        "COMPLETE"
        if not missing and cpu.get("status") == "SETTLED" and not errors
        else "INCOMPLETE"
    )
    result = dict(
        schema="independent-evaluation-result.v1",
        status=status,
        errors=errors,
        evaluation_id=contract["evaluation_id"],
        job_id=os.environ["SLURM_JOB_ID"],
        contract_sha256=fingerprint(contract),
        manifest_sha256=fingerprint(manifest),
        rows=rows,
        scores=scores,
        leaderboard=leaderboard(manifest, scores),
        cpu_receipt=cpu,
        model_calls=0,
        agent_budget_charge_seconds=0.0,
        exact_allocation_cost_pending_sacct=True,
        finished=time.time(),
    )
    atomic(root / "evaluation_result.json", result)
    closed = dict(
        status=status,
        job_id=os.environ["SLURM_JOB_ID"],
        evaluation_id=contract["evaluation_id"],
        result_sha256=file_sha(root / "evaluation_result.json"),
        errors=errors,
        at=time.time(),
    )
    atomic(root / "closed.json", closed)
    return closed


def execute(root, expected_sha):
    from kinetic_agents.release import verify
    from kinetic_agents.execution.cpu import run as cpu_run

    root = plain(root)
    contract = read(root / "evaluation_contract.json")
    if fingerprint(contract) != expected_sha or str(root) != contract["remote_root"]:
        raise PermissionError("unbound evaluator contract")
    if (
        os.environ.get("SLURM_JOB_NAME") != contract["evaluation_id"]
        or os.environ.get("SLURM_NTASKS") != "64"
        or os.environ.get("SLURM_JOB_NUM_NODES") != "1"
    ):
        raise PermissionError("one-node 64-task owned allocation required")
    with locked(root / "run.lock"):
        if (root / "started.json").exists():
            raise PermissionError("execution already attempted; no silent reset")
        now = time.time()
        atomic(
            root / "started.json",
            dict(
                at=now,
                job_id=os.environ["SLURM_JOB_ID"],
                compute_deadline=now + contract["allocation_minutes"] * 60 - 60,
            ),
        )
        errors = []
        try:
            package = verify(root / "release")
            if package["release_sha256"] != contract["release_sha256"]:
                raise PermissionError("release differs from registration")
            manifest = verify_inputs(root, contract)
            import cantera as ct
            from kinetic_agents.evaluation._frozen.mechrl.physical_tools import (
                native_chemistry_guard,
            )

            if ct.__version__ != manifest["solver_version"]:
                raise PermissionError("solver version differs from registered benchmark")
            guard = native_chemistry_guard()
            if len(os.sched_getaffinity(0)) < contract["workers"]:
                raise PermissionError("insufficient assigned cores for evaluator workers")
            atomic(
                root / "qualification.json",
                dict(
                    status="PASS",
                    solver_version=ct.__version__,
                    chemistry_guard=guard,
                    source_pins=source_pins(),
                    model_calls=0,
                ),
            )
            for name in ("attempts", "receipts", "cache"):
                (root / name).mkdir()
            cpu_run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "kinetic_agents.evaluation.service",
                    "compute",
                    "--root",
                    str(root),
                ],
                root / "cpu.json",
                contract["allocation_minutes"] * 60 * 64,
                64,
                read(root / "started.json")["compute_deadline"],
            )
        except BaseException as exc:
            errors.append(dict(phase="compute", type=type(exc).__name__, message=str(exc)[:1000]))
        try:
            return finish(root, contract, errors)
        except BaseException as exc:
            errors.append(dict(phase="finalize", type=type(exc).__name__, message=str(exc)[:1000]))
            closed = dict(
                status="FAILED_FINALIZATION",
                job_id=os.environ["SLURM_JOB_ID"],
                errors=errors,
                at=time.time(),
            )
            atomic(root / "closed.json", closed)
            return closed


def observe(root):
    """Read-only observation; never creates work or advances the evaluator."""
    root = plain(root)
    binding = read(root / "binding.json")
    result = {"scheduler": scheduler(binding)}
    for name in ("started.json", "qualification.json", "progress.json", "closed.json"):
        target = remote_path(binding["remote_root"]) / name
        from kinetic_agents.execution.slurm import ssh

        response = ssh(
            "test ! -L " + str(target) + " && test -f " + str(target) + " && cat " + str(target)
        )
        if response.returncode == 0:
            value = json.loads(response.stdout)
            if "job_id" in value and value["job_id"] != binding["job_id"]:
                raise PermissionError("foreign observer receipt")
            result[name.removesuffix(".json")] = value
    atomic(root / "observation.json", result)
    return result


def fetch(root):
    import subprocess

    root = plain(root)
    observed = observe(root)
    binding = read(root / "binding.json")
    if observed["scheduler"]["state"] not in ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"):
        raise PermissionError("wait for terminal allocation before final download")
    closed = observed.get("closed", {})
    digest = closed.get("result_sha256")
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise PermissionError("no verified final result; retain failure without fake score")
    target = root / "evaluation_result.json"
    temporary = root / ("result-" + digest + ".download")
    source = remote_path(binding["remote_root"]) / "evaluation_result.json"
    if target.exists():
        if file_sha(target) != digest:
            raise PermissionError("local result differs from remote final commitment")
    else:
        response = subprocess.run(
            ["scp", "-q", "sca2070:" + str(source), str(temporary)], timeout=120
        )
        if response.returncode or file_sha(temporary) != digest:
            raise ConnectionError("final result download or hash verification failed")
        temporary.replace(target)
    value = read(target)
    contract = read(root / "evaluation_contract.json")
    if value["job_id"] != binding["job_id"] or value["contract_sha256"] != fingerprint(contract):
        raise PermissionError("foreign result identity")
    manifest = verify_inputs(root, contract)
    recomputed = summarize(manifest, value["rows"])
    board = leaderboard(manifest, recomputed)
    if recomputed != value["scores"] or board != value["leaderboard"]:
        raise ValueError("host score/leaderboard recomputation mismatch")
    atomic(
        root / "scorecard.json",
        dict(
            status=value["status"],
            scores=recomputed,
            leaderboard=board,
            result_sha256=digest,
            model_calls=0,
            agent_budget_charge_seconds=0.0,
            evaluation_allocated_core_seconds=observed["scheduler"]["allocated_core_seconds"],
            evaluation_process_cpu_seconds=value["cpu_receipt"].get("cpu_seconds"),
            scientific_interpretation="terminal full-pool evaluation, not RSI proof or pristine blind test",
        ),
    )
    atomic(
        root / "evaluation_state.json",
        dict(
            status=value["status"],
            **binding,
            account="independent_evaluation",
            agent_budget_charged=False,
            model_calls=0
        ),
    )
    return read(root / "scorecard.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=["register", "deploy", "submit", "execute", "compute", "observe", "fetch"],
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--contract-sha")
    args = parser.parse_args()
    if args.operation == "register":
        outcome = register(args.root, args.release)
    elif args.operation == "execute":
        outcome = execute(args.root, args.contract_sha)
    else:
        outcome = globals()[args.operation](args.root)
    print(json.dumps(outcome, allow_nan=False))
