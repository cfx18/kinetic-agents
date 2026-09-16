"""Extracted reusable implementation; historical launchers intentionally excluded."""

import hashlib, json, sys, time
from pathlib import Path
from kinetic_agents.execution.cpu import run as cpu_run
from kinetic_agents.core.storage import atomic


def finalize(root, store, service):
    with service.store.connection() as db:
        rows = db.execute(
            "SELECT value FROM heads WHERE kind='submission' AND id='final'"
        ).fetchall()
    submission = None
    if rows:
        final = json.loads(rows[0][0])
        prefix = "Locked final file manifest "
        if not final["summary"].startswith(prefix):
            raise PermissionError("unlocked team finish")
        ident = final["summary"][len(prefix) :]
        if len(ident) != 64 or any(c not in "0123456789abcdef" for c in ident):
            raise PermissionError("invalid final identity")
        submission = json.loads((root / "final_artifacts" / (ident + ".json")).read_text())
        for item in submission["mechanisms"] + [submission["report"]]:
            if (
                hashlib.sha256((root / "final_artifacts" / item["sha256"]).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise PermissionError("locked final bytes changed")
        atomic(root / "submission.json", submission)
        if store.get("status") == "FINALIZING":
            store.advance("artifacts_verified")
    result = dict(
        status=store.get("status"),
        submission=submission,
        model_account=store.snapshot()["api"],
        search_deadline=store.get("deadline"),
        evaluator_account="separate",
        scientifically_verified=False,
        at=time.time(),
    )
    atomic(root / "result.json", result)
    return result


def postrun_review(root, owner, result):
    """Also reached when scientific finalization raises; never masks that error."""
    # Same bounded evaluator/local-overhead owner; not another allowance.
    release = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-B",
        "-m",
        "kinetic_agents.observability.review",
        "finish",
        "--root",
        str(root),
        "--release",
        str(release),
    ]
    if result.get("submission") and owner["status"] == "SETTLED":
        command.append("--evaluate")
    try:
        from kinetic_agents.core.store import Store

        prior = Store(root / "runtime.sqlite").get("recovery_prior_evaluator_cpu_seconds", 0)
        evaluation = cpu_run(
            command, root / "evaluator_local_cpu.json", 3600 - prior, 2, time.time() + 86400
        )
        atomic(
            root / "evaluator_owner_finished.json",
            dict(
                status=evaluation["status"],
                cpu_seconds=evaluation["cpu_seconds"],
                account="independent_evaluation",
                charged_to_search=False,
                includes_postmortem=True,
                at=time.time(),
            ),
        )
    except Exception as exc:
        atomic(
            root / "review_build_status.json",
            dict(
                status="PENDING_RETRY",
                reason="postrun_owner_failed",
                error_type=type(exc).__name__,
                original_result_unchanged=True,
                at=time.time(),
            ),
        )
