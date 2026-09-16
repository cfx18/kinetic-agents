"""One explicit bulk/gas ingress repair of an ended, locked submission.

Preserves the original endpoint, reuses ONLY its verified parent rows, and
spends its remaining evaluator allowance. No model/search process is started.
The detached owner submits, receives and combines results without a chat monitor.
This is deliberately not a generic benchmark retry or score-dependent repair API.
"""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from kinetic_agents.core.paths import plain, file_sha
from kinetic_agents.core.storage import atomic, locked
from kinetic_agents.evaluation import service
from kinetic_agents.evaluation.artifacts import inline_yaml
from kinetic_agents.evaluation.scoring import fingerprint, summarize
from kinetic_agents.evaluation.submission import observe_with_retries
from kinetic_agents.release import verify

REVISION = "endpoint_bulk_v1"
LOCAL_RESERVATION = 3600.0
OWNER_LIMIT = 3500.0  # The other 100 seconds remain reserved for launch/settlement.
TERMINAL = {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}


def budget(original_cap, allocated, local_cpu):
    values = (original_cap, allocated, local_cpu)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("finite nonnegative evaluator accounting required")
    if original_cap != 64 * 3600:
        raise PermissionError("this repair is limited to the existing 64-core-hour account")
    remaining = original_cap - allocated - local_cpu
    minutes = math.floor((remaining - LOCAL_RESERVATION) / (60 * 64))
    if minutes < 2:
        raise PermissionError("insufficient remaining evaluator allowance; no new job")
    return dict(
        original_cap_core_seconds=original_cap,
        original_allocated_core_seconds=allocated,
        original_local_cpu_seconds=local_cpu,
        remaining_cap_core_seconds=remaining,
        repair_local_reservation_seconds=LOCAL_RESERVATION,
        repair_allocation_minutes=minutes,
        maximum_total_charge_seconds=allocated + local_cpu + LOCAL_RESERVATION + minutes * 60 * 64,
        account="independent_evaluation",
        agent_budget_charge_seconds=0,
        budget_reset=False,
    )


def verified_result(target):
    """Verify downloaded bytes, receipt identities, pool and recomputed scores."""
    contract = service.read(target / "evaluation_contract.json")
    manifest = service.verify_inputs(target, contract)
    binding = service.read(target / "binding.json")
    observed = service.read(target / "observation.json")
    card = service.read(target / "scorecard.json")
    digest = file_sha(target / "evaluation_result.json")
    result = service.read(target / "evaluation_result.json")
    scheduler = observed["scheduler"]
    if (scheduler["state"] not in TERMINAL or scheduler["allocated_cpus"] != 64
            or scheduler["job_id"] != binding["job_id"]
            or scheduler["job_name"] != contract["evaluation_id"]
            or binding["contract_sha256"] != fingerprint(contract)
            or binding["remote_root"] != contract["remote_root"]
            or observed["closed"]["job_id"] != binding["job_id"]
            or result["job_id"] != binding["job_id"]
            or result["contract_sha256"] != fingerprint(contract)
            or result["manifest_sha256"] != fingerprint(manifest)
            or digest != observed["closed"]["result_sha256"]
            or digest != card["result_sha256"]
            or card["evaluation_allocated_core_seconds"] != scheduler["allocated_core_seconds"]):
        raise PermissionError("endpoint identity/hash/accounting mismatch")
    scores = summarize(manifest, result["rows"])
    if scores != result["scores"] or scores != card["scores"]:
        raise PermissionError("endpoint scores do not match original rows")
    if service.leaderboard(manifest, scores) != result["leaderboard"]:
        raise PermissionError("endpoint leaderboard mismatch")
    return contract, manifest, result, scheduler


def original(root):
    root = plain(root)
    contract, manifest, result, scheduler = verified_result(root / "endpoint")
    ended = service.read(root / "result.json")
    submitted = service.read(root / "submission.json")
    local = service.read(root / "evaluator_local_cpu.json")
    if (local["status"] != "SETTLED"
            or service.read(root / "local_cpu.json")["status"] != "SETTLED"
            or ended.get("status") != "COMPLETED" or ended.get("submission") != submitted
            or file_sha(root / "submission.json") != manifest["source_submission_sha256"]):
        raise PermissionError("search and original evaluator must be ended and settled")
    if set(manifest["selected"]) != {"parent"} or result["status"] != "COMPLETE":
        raise PermissionError("this repair only supplements a complete parent-only ingress failure")
    invalid = manifest.get("invalid_candidates", [])
    expected = {f"candidate_{i + 1}": row["sha256"] for i, row in enumerate(submitted["mechanisms"])}
    if (not 1 <= len(expected) <= 3 or len(invalid) != len(expected)
            or {row["label"]: row["sha256"] for row in invalid} != expected
            or any(row["status"] != "invalid_mechanism" for row in invalid)):
        raise PermissionError("original rejection set differs from the locked submission")
    return contract, manifest, result, budget(
        contract["evaluation_cap_core_seconds"], scheduler["allocated_core_seconds"], local["cpu_seconds"]
    )


def launch(root, release):
    """One exclusive revision per original run; never silently retry sbatch."""
    root, release = plain(root), plain(release)
    package = verify(release)
    with locked(root / "endpoint_repair.lock"):
        contract, manifest, _, account = original(root)
        if package["files"]["kinetic_agents/evaluation/scoring.py"] != manifest["endpoint_code_sha256"]:
            raise PermissionError("scoring changed; not an ingress-only repair")
        target = root / REVISION
        target.mkdir(mode=0o700)  # Refuse re-launch, including ambiguous previous attempts.
        request = dict(
            schema="bulk-ingress-repair.v1", root=str(root), release=str(release),
            release_sha256=package["release_sha256"], at=time.time(),
            original_contract_sha256=fingerprint(contract),
            original_manifest_sha256=fingerprint(manifest),
            original_result_sha256=file_sha(root / "endpoint/evaluation_result.json"),
            original_submission_sha256=file_sha(root / "submission.json"),
            budget=account, scientist_restart_allowed=False, model_calls=0,
        )
        atomic(target / "repair_request.json", request)
        env = dict(os.environ, PYTHONPATH=str(release), PYTHONDONTWRITEBYTECODE="1")
        with (target / "owner.log").open("xb") as stream:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "kinetic_agents.evaluation.repair", "supervise",
                 "--root", str(target)], stdin=subprocess.DEVNULL, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True, env=env,
            )
        receipt = dict(status="OWNER_STARTED", pid=process.pid, target=str(target),
                       at=time.time(), model_calls=0, budget=account)
        atomic(target / "launch.json", receipt)
        return receipt


def prepare(target):
    import cantera as ct
    from kinetic_agents.evaluation._frozen.mechrl.physical_tools import native_chemistry_guard

    target = plain(target)
    request = service.read(target / "repair_request.json")
    root = plain(request["root"])
    if target != root / REVISION:
        raise PermissionError("foreign repair target")
    old_contract, old_manifest, _, account = original(root)
    if (fingerprint(old_contract) != request["original_contract_sha256"]
            or fingerprint(old_manifest) != request["original_manifest_sha256"]
            or file_sha(root / "endpoint/evaluation_result.json") != request["original_result_sha256"]
            or file_sha(root / "submission.json") != request["original_submission_sha256"]
            or account != request["budget"]):
        raise PermissionError("original endpoint changed after repair reservation")
    package = verify(request["release"])
    if package["release_sha256"] != request["release_sha256"]:
        raise PermissionError("repair release changed")
    if ct.__version__ != old_manifest["solver_version"]:
        raise PermissionError("native solver version changed")
    guard = native_chemistry_guard()
    selected = {}
    acceptance = []
    for row in old_manifest["invalid_candidates"]:
        label, digest = row["label"], row["sha256"]
        source = plain(root / "final_artifacts" / digest)
        if file_sha(source) != digest or file_sha(root / "endpoint" / (label + ".yaml")) != digest:
            raise PermissionError("locked candidate bytes changed")
        data = inline_yaml(source)
        if data["phases"][0]["kinetics"] != "bulk":
            raise PermissionError("candidate is not affected by the specific bulk ingress defect")
        gas = ct.Solution(str(source))
        destination = target / (label + ".yaml")
        with destination.open("xb") as stream:
            stream.write(source.read_bytes())
        if file_sha(destination) != digest:
            raise PermissionError("candidate copy mismatch")
        selected[label] = dict(id="endpoint-" + digest, sha256=digest, path=destination.name,
                               species_count=gas.n_species, reaction_count=gas.n_reactions,
                               keep=gas.species_names)
        acceptance.append(dict(label=label, sha256=digest, species=gas.n_species,
                               reactions=gas.n_reactions, unchanged_bytes=True))
    manifest = {**old_manifest, "selected": selected, "invalid_candidates": [],
                "endpoint_pairs": 610 * len(selected),
                "repair": dict(reason="bulk_gas_and_plain_text_header_compatibility", parent_recomputed=False,
                               original_manifest_sha256=fingerprint(old_manifest))}
    atomic(target / "acceptance.json", dict(status="PASS", solver_version=ct.__version__,
           native_guard=guard, candidates=acceptance, numerical_solves=0))
    atomic(target / "manifest.json", manifest)
    atomic(target / "commitment.json", dict(manifest_sha256=fingerprint(manifest)))
    service.ID = old_contract["evaluation_id"] + "_bulk_v1"
    service.REMOTE = service.BASE / service.ID
    service.PYTHON = Path(old_contract["python"])
    service.MINUTES = account["repair_allocation_minutes"]
    service.WORKERS = old_contract["workers"]
    return service.register(target, request["release"],
        cap_seconds=account["remaining_cap_core_seconds"], budget_lineage={
            **account, "original_contract_sha256": fingerprint(old_contract),
            "original_result_sha256": request["original_result_sha256"]})


def combine(target):
    request = service.read(target / "repair_request.json")
    root = Path(request["root"])
    _, old_manifest, old_result, account = original(root)
    _, manifest, result, scheduler = verified_result(target)
    if (file_sha(root / "endpoint/evaluation_result.json") != request["original_result_sha256"]
            or fingerprint(old_manifest) != request["original_manifest_sha256"]):
        raise PermissionError("original result changed before combination")
    # The complete manifest other than the selection and explicit repair note is identical.
    expected = {**old_manifest, "selected": manifest["selected"], "invalid_candidates": [],
                "endpoint_pairs": 610 * len(manifest["selected"]), "repair": manifest["repair"]}
    if expected != manifest or set(old_manifest["selected"]) & set(manifest["selected"]):
        raise PermissionError("pool/solver/scoring changed or duplicate selection")
    selected = {**old_manifest["selected"], **manifest["selected"]}
    merged_manifest = {**manifest, "selected": selected, "endpoint_pairs": 610 * len(selected)}
    rows = old_result["rows"] + result["rows"]
    scores = summarize(merged_manifest, rows)
    sources = [dict(attempt="original", result_sha256=request["original_result_sha256"], labels=["parent"]),
               dict(attempt=REVISION, result_sha256=file_sha(target / "evaluation_result.json"),
                    labels=list(manifest["selected"]))]
    charge = (account["original_allocated_core_seconds"] + account["original_local_cpu_seconds"]
              + scheduler["allocated_core_seconds"] + LOCAL_RESERVATION)
    if charge > account["original_cap_core_seconds"]:
        raise PermissionError("combined evaluator accounting exceeded original envelope")
    card = dict(
        status=result["status"], scores=scores, leaderboard=service.leaderboard(merged_manifest, scores),
        row_sources=sources, invalid_candidates=[], model_calls=0, agent_budget_charge_seconds=0,
        budget={**account, "repair_allocated_core_seconds": scheduler["allocated_core_seconds"],
                "combined_charge_upper_bound_seconds": charge,
                "remaining_guaranteed_seconds": account["original_cap_core_seconds"] - charge},
        evaluation_allocated_core_seconds=account["original_allocated_core_seconds"] + scheduler["allocated_core_seconds"],
        evaluation_process_cpu_seconds=old_result["cpu_receipt"]["cpu_seconds"] + result["cpu_receipt"]["cpu_seconds"],
        scientific_interpretation="same locked submission; 610 historically exposed cases, not RSI proof or pristine blind test",
        parent_reuse="only this submission's original verified parent rows; no cross-run cache",
        original_results_preserved=True, optimization_feedback_allowed=False,
    )
    atomic(target / "combined_manifest.json", merged_manifest)
    atomic(target / "combined_result.json", dict(rows=rows, scores=scores, row_sources=sources,
                                               leaderboard=card["leaderboard"]))
    card["combined_result_sha256"] = file_sha(target / "combined_result.json")
    atomic(target / "combined_scorecard.json", card)
    lines = ["# 兼容修复后的独立评测", "", "仅补评原锁定候选，不重开搜索，不改评分。原 endpoint/ 完整保留。", "",
             "| 候选 | 物种 / 反应 | 成功 / 610 | 全池平均绝对标准化误差 | 成功子集均值 |", "|---|---:|---:|---:|---:|"]
    for label, item in selected.items():
        score = scores[label]["all610"]
        full = score["full_pool_mean_abs_sigma"]
        partial = score["successful_subset_mean_abs_sigma"]
        lines.append(f"| {label} | {item['species_count']} / {item['reaction_count']} | {score['success']} / 610 | "
                     + (f"{full:.6f}" if full is not None else "不可报告（存在失败或缺失）")
                     + " | " + (f"{partial:.6f}" if partial is not None else "无成功结果") + " |")
    lines += ["", "误差是 abs(signed_sigma)，不是百分比；不完整候选不能用成功子集均值冒充全池均值。",
              "610 个工况是历史已暴露评测池，不是新盲测；本轮不证明 RSI 或单/多 Agent 因果收益。", "",
              f"累计分配核时：{card['evaluation_allocated_core_seconds']/3600:.6f}；"
              f"含本地预留的累计计费上界：{charge/3600:.6f} / 64 核时。",
              "模型调用 0；搜索账户扣费 0。进程 CPU、本地所有者实际 CPU 与分配核时分开记录。", "",
              "- combined_scorecard.json：分组评分、覆盖率、原始结果来源和预算。",
              "- combined_result.json：全部逐 case 原始结果；row_sources 标明原评测父机理与本次补评的来源。",
              "- acceptance.json：原始候选加载与哈希验收；不是科学精度证明。", ""]
    (target / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return card


def execute(target):
    with locked(target / "worker.lock"):
        if (target / "worker_started.json").exists():
            raise PermissionError("explicit repair cannot be silently retried")
        atomic(target / "worker_started.json", dict(at=time.time(), pid=os.getpid()))
        prepare(target)
        service.deploy(target)
        service.submit(target)
        while True:
            observed = observe_with_retries(target)
            if observed["scheduler"]["state"] in TERMINAL:
                service.fetch(target)
                return combine(target)
            if observed["scheduler"]["state"] in {"OUT_OF_MEMORY", "NODE_FAIL"}:
                raise RuntimeError("terminal infrastructure failure retained; no automatic resubmit")
            time.sleep(30)  # Owned background receiver, independent of this conversation.


def supervise(target):
    from kinetic_agents.execution.cpu import run

    try:
        receipt = run([sys.executable, "-B", "-m", "kinetic_agents.evaluation.repair",
                       "execute", "--root", str(target)], target / "local_cpu.json",
                      OWNER_LIMIT, 2, time.time() + 86400)
        complete = (receipt.get("reason") == "child_exit" and receipt["returncode"] == 0
                    and (target / "combined_scorecard.json").is_file())
        state = dict(status="COMPLETE" if complete else "FAILED",
                     cpu=receipt, at=time.time(), model_calls=0, charged_to_search=False)
    except Exception as exc:
        state = dict(status="FAILED", error_type=type(exc).__name__, at=time.time(), model_calls=0)
    atomic(target / "owner_finished.json", state)
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("launch", "supervise", "execute"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    args = parser.parse_args()
    target = plain(args.root)
    try:
        outcome = launch(target, args.release) if args.operation == "launch" else globals()[args.operation](target)
    except Exception as exc:
        if args.operation == "execute":
            atomic(target / "repair_failure.json", dict(error_type=type(exc).__name__,
                   status="FAILED", at=time.time(), new_search_started=False))
        raise
    print(json.dumps({k: v for k, v in outcome.items() if k not in ("scores", "leaderboard")}, allow_nan=False))
