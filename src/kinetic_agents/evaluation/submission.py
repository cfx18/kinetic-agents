"""Adapt locked new-team finals to the EXISTING independent 610-case evaluator.

Only host code reads the evaluation capsule. No evaluator result is returned to
an active scientist. Two accounts at 64 core-hours each fit the approved 128 total.
No scoring, chemistry, benchmark exclusion or retry policy is changed here.
"""

from kinetic_agents.core.inputs import task_directory
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from kinetic_agents.evaluation.pins import source_pins
from kinetic_agents.core.storage import atomic
import kinetic_agents.evaluation.service as evaluator
from kinetic_agents.evaluation.scoring import validate_pool
from kinetic_agents.evaluation.scoring import fingerprint
from kinetic_agents.core.paths import file_sha
from kinetic_agents.execution.slurm import BASE


def prepare_base(pair, source):
    """Before search: bind evaluation data/definitions but compute no science."""
    import cantera as ct

    pair = Path(pair).absolute()
    source = Path(source).absolute()
    if (pair / "evaluation_base.json").exists():
        raise FileExistsError("evaluation base already committed")
    pool = json.loads(source.read_text())
    validate_pool(pool)
    if pool["solver_version"] != ct.__version__ or pool["source_pins"] != source_pins():
        raise PermissionError(
            "benchmark solver/source mismatch; do not silently update the scoring contract"
        )
    value = {
        k: pool[k]
        for k in (
            "development",
            "recheck",
            "numerical_policy",
            "solver_version",
            "source_pins",
            "per_case_cpu",
        )
    }
    value.update(
        source_manifest_sha256=file_sha(source),
        feedback_to_search=False,
        case_count=610,
        exposure="historically_exposed_not_pristine_blind",
        total_cpu_cap_seconds=128 * 3600,
    )
    atomic(pair / "evaluation_base.json", value)
    qualification = dict(
        status="PASS",
        base_sha256=file_sha(pair / "evaluation_base.json"),
        solver_version=ct.__version__,
        source_pins=source_pins(),
        scientific_solver_calls=0,
        model_calls=0,
        evaluator="existing endpoint_service; source and scoring unchanged",
        per_arm_cpu_cap_seconds=64 * 3600,
        needs_ended_locked_submission=True,
    )
    atomic(pair / "qualification/endpoint.json", qualification)
    return qualification


def identify(arm, version="v1"):
    if arm not in ("solo-max", "team-max"):
        raise PermissionError("approved arm required")
    if version not in ("v1", "v2"):
        raise PermissionError("explicit evaluator attempt version required")
    ident = "cfx_" + arm.replace("-", "_") + "_endpoint_20260914_" + version
    evaluator.ID = ident
    evaluator.REMOTE = BASE / ident
    evaluator.MINUTES = 58  # Preserve local overhead + Slurm shutdown margin under 64 core-hours.
    return ident


def identify_root(root):
    """New standalone runs never reuse the historical pair's remote job name."""
    import re

    plan = json.loads((Path(root).parent / "preflight.json").read_text())
    contract = plan["contracts"][Path(root).name]
    if contract.get("mode") in (
        "astra-subscription-solo-v1",
        "astra-subscription-team-v1",
        "native-harness-solo-v2",
        "native-harness-team-v2",
    ):
        ident = contract.get("evaluation_id", "")
        if not re.fullmatch(r"cfx_astra_[a-f0-9]{24}_endpoint", ident):
            raise PermissionError("unique owned standalone evaluator identity required")
        if contract.get("evaluation_cpu_seconds") != 64 * 3600:
            raise PermissionError("standalone evaluator requires its separate bounded account")
        evaluator.ID = ident
        evaluator.REMOTE = BASE / ident
        evaluator.MINUTES = 58
        if "deployment" in contract:
            from kinetic_agents.execution.deployment import validate_deployment

            evaluator.PYTHON = BASE.__class__(
                validate_deployment(contract["deployment"])["evaluation_python"]
            )
        return ident
    return identify(Path(root).name, "v2" if Path(root).parent.name.endswith("-v2") else "v1")


def prepare(root, release):
    import cantera as ct
    from kinetic_agents.evaluation.artifacts import inline_yaml, failure_diagnostic
    from kinetic_agents.release import verify

    root = Path(root).absolute()
    release = Path(release).absolute()
    identify_root(root)
    target = root / "endpoint"
    target.mkdir(mode=0o700)
    account = json.loads((root / "local_cpu.json").read_text())
    ended = json.loads((root / "result.json").read_text())
    if account["status"] != "SETTLED" or not ended.get("submission"):
        raise PermissionError("settled and locked scientific submission required")
    submission = ended["submission"]
    if submission != json.loads((root / "submission.json").read_text()):
        raise PermissionError("submission identity changed")
    base = json.loads((root.parent / "evaluation_base.json").read_text())
    qualification = json.loads((root.parent / "qualification/endpoint.json").read_text())
    if file_sha(root.parent / "evaluation_base.json") != qualification["base_sha256"]:
        raise PermissionError("fixed evaluator data changed")
    if ct.__version__ != base["solver_version"] or source_pins() != base["source_pins"]:
        raise PermissionError("evaluation science changed")
    plan = json.loads((root.parent / "preflight.json").read_text())
    selected = {}
    invalid = []
    inputs = [
        (
            "parent",
            task_directory(root) / "parent.yaml",
            plan["contracts"][root.name]["parent_sha256"],
        )
    ]
    inputs.extend(
        (f"candidate_{i+1}", root / "final_artifacts" / row["sha256"], row["sha256"])
        for i, row in enumerate(submission["mechanisms"])
    )
    if len(inputs) > 4:
        raise PermissionError("at most three final mechanisms")
    for label, path, expected in inputs:
        if path.is_symlink() or file_sha(path) != expected:
            raise PermissionError("locked mechanism bytes changed")
        blob = path.read_bytes()
        candidate_path = target / (label + ".yaml")
        with candidate_path.open("xb") as stream:
            stream.write(blob)
        stage = "yaml_ingress"
        try:
            inline_yaml(candidate_path)
            stage = "cantera_load"
            gas = ct.Solution(str(candidate_path))
        except Exception as exc:
            if label == "parent":
                raise
            invalid.append(
                dict(
                    label=label,
                    sha256=expected,
                    status="invalid_mechanism",
                    **failure_diagnostic(exc, stage),
                    coverage=0,
                    full_pool_mean_abs_sigma=None,
                )
            )
            continue
        selected[label] = dict(
            id="endpoint-" + expected,
            sha256=expected,
            path=label + ".yaml",
            species_count=gas.n_species,
            reaction_count=gas.n_reactions,
            keep=gas.species_names,
        )
    manifest = {
        k: base[k]
        for k in (
            "development",
            "recheck",
            "numerical_policy",
            "solver_version",
            "source_pins",
            "per_case_cpu",
        )
    }
    manifest.update(
        schema="openworld-final-pool.v1",
        status="PREPARED",
        selected=selected,
        endpoint_pairs=len(selected) * 610,
        endpoint_code_sha256=verify(release)["files"]["kinetic_agents/evaluation/scoring.py"],
        source_submission_sha256=file_sha(root / "submission.json"),
        source_pool_manifest_sha256=base["source_manifest_sha256"],
        pristine_final_holdout=False,
        controller_feedback_allowed=False,
        scientist_restart_allowed=False,
        score_dependent_case_exclusion=False,
        cross_run_solver_cache=False,
        invalid_candidates=invalid,
    )
    atomic(target / "manifest.json", manifest)
    atomic(target / "commitment.json", dict(manifest_sha256=fingerprint(manifest)))
    evaluator.register(target, release)
    return target


def observe_with_retries(target, *, attempts=4, sleep=time.sleep):
    """Retry read-only verification; never accept, cancel or resubmit a mismatch.

    Scheduler accounting can briefly be inconsistent after submission. The
    original failed sample remains diagnostic, not proof of a foreign job or
    permission to skip identity verification. Persistent mismatches still fail.
    """
    if type(attempts) != int or not 1 <= attempts <= 4:
        raise ValueError("bounded receiver retries required")
    failures = []
    for attempt in range(attempts):
        try:
            result = evaluator.observe(target)
        except (PermissionError, ConnectionError, subprocess.TimeoutExpired) as exc:
            failures.append(
                dict(at=time.time(), attempt=attempt + 1, error_type=type(exc).__name__)
            )
            atomic(
                Path(target) / "receiver_status.json",
                dict(
                    status="BLOCKED" if attempt + 1 == attempts else "VERIFYING_AGAIN",
                    failures=failures,
                    verified=False,
                    new_remote_jobs=0,
                ),
            )
            if attempt + 1 == attempts:
                raise
            sleep(2**attempt)
        else:
            if failures:
                atomic(
                    Path(target) / "receiver_status.json",
                    dict(
                        status="RECOVERED",
                        failures=failures,
                        verified=True,
                        new_remote_jobs=0,
                    ),
                )
            return result


def execute(root, release):
    """Called after search/CPU settlement by its coordinator, never a monitor."""
    root = Path(root).absolute()
    identify_root(root)
    target = root / "endpoint"
    if not target.exists():
        prepare(root, release)
    if not (target / "deployment.json").exists():
        evaluator.deploy(target)
    if not (target / "submission_intent.json").exists():
        evaluator.submit(target)
    if not (target / "binding.json").exists():
        raise PermissionError("ambiguous evaluator submit; reconcile exact name")
    while True:
        observed = observe_with_retries(target)
        if observed["scheduler"]["state"] in (
            "COMPLETED",
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
            "OUT_OF_MEMORY",
            "NODE_FAIL",
        ):
            scorecard = evaluator.fetch(target)
            scorecard["invalid_candidates"] = json.loads(
                (target / "manifest.json").read_text()
            ).get("invalid_candidates", [])
            atomic(target / "scorecard.json", scorecard)
            return scorecard
        time.sleep(30)  # Background owner only; chat observer is not required.


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=("prepare-base", "execute"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--source", type=Path)
    p.add_argument("--release", type=Path)
    a = p.parse_args()
    v = (
        prepare_base(a.root, a.source)
        if a.operation == "prepare-base"
        else execute(a.root, a.release)
    )
    print(json.dumps(v))
