"""Independent, non-solving audit of a TERMINAL open-world submission.

Never imports the scientist's code or runs its scripts. Uses the established
native Cantera element guard and loader. No benchmark cases are opened and no
accuracy is certified merely because a mechanism loads.
"""

import argparse
import json
from pathlib import Path
import warnings

from kinetic_agents.core.storage import atomic
from kinetic_agents.core.paths import file_sha
from kinetic_agents.core.paths import plain


class MechanismInputError(ValueError):
    """Stable ingress classification, not a solver/chemistry failure."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def failure_diagnostic(exc, stage):
    """Do not echo arbitrary YAML or native exception payloads into metadata."""
    return dict(
        error_type=type(exc).__name__,
        failure_stage=stage,
        reason_code=exc.code if isinstance(exc, MechanismInputError) else (
            "native_load_failed" if stage == "cantera_load" else "ingress_validation_failed"
        ),
        message=str(exc) if isinstance(exc, MechanismInputError) else (
            "Cantera rejected the original mechanism; no case was solved."
            if stage == "cantera_load" else "Mechanism ingress validation failed; no case was solved."
        ),
        numerical_solves=0,
    )


def inline_yaml(path):
    import yaml

    path = plain(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise MechanismInputError("file_bounds", "bounded mechanism file required")
    try:
        data = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, UnicodeError):
        raise MechanismInputError("malformed_yaml", "mechanism YAML cannot be parsed") from None
    allowed = {
        "description",
        "generator",
        "cantera-version",
        "git-commit",
        "date",
        "input-files",
        "units",
        "phases",
        "species",
        "reactions",
        "design",
        "parent",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        raise MechanismInputError("unsupported_metadata", "unsupported mechanism metadata/extensions; manual review required")
    # Descriptive user header data is not an import/reference or executable
    # extension. Do not interpret these strings as paths or physics settings.
    # Unknown fields and structured objects remain rejected.
    for key in ("design", "parent"):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 4096):
            raise MechanismInputError("invalid_annotation", "design/parent annotations must be bounded plain text")
    for key in ("species", "reactions"):
        if (
            not isinstance(data.get(key), list)
            or not data[key]
            or any(not isinstance(x, dict) for x in data[key])
        ):
            raise MechanismInputError("external_or_noninline_data", "self-contained inline species and reactions required")
    phases = data.get("phases")
    if not isinstance(phases, list) or len(phases) != 1 or not isinstance(phases[0], dict):
        raise MechanismInputError("phase_count", "single self-contained gas phase required")
    phase = phases[0]
    if phase.get("thermo") != "ideal-gas":
        raise MechanismInputError("unsupported_thermo", "ideal-gas thermodynamics required")
    # Cantera documents gas as an alias for bulk. YamlWriter 3.2 emits bulk.
    # Accept both WITHOUT rewriting the submitted bytes; native loading still
    # enforces kinetics/element consistency with the pinned solver version.
    # https://www.cantera.org/3.2/yaml/phases.html#kinetics
    if phase.get("kinetics") not in ("gas", "bulk"):
        raise MechanismInputError("unsupported_kinetics", "gas or bulk kinetics required for the single ideal-gas phase")
    if (
        phase.get("reactions", "all") != "all"
        or not isinstance(phase.get("species"), list)
        or any(not isinstance(x, str) for x in phase["species"])
        or "adjacent-phases" in phase
    ):
        raise MechanismInputError("external_phase_reference", "external phase/species/reaction references are not permitted")
    return data


def audit_mechanism(parent_path, candidate_path):
    import cantera as ct
    from kinetic_agents.evaluation._frozen.mechrl.physical_tools import native_chemistry_guard

    inline_yaml(parent_path)
    inline_yaml(candidate_path)
    guard = native_chemistry_guard()
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        parent = ct.Solution(str(parent_path))
        parent_notices = len(notices)
        candidate = ct.Solution(str(candidate_path))
    parent_species = {s.name: s.input_data for s in parent.species()}
    changed = [
        s.name
        for s in candidate.species()
        if s.name not in parent_species or s.input_data != parent_species[s.name]
    ]

    def physics(data):
        return {k: v for k, v in data.items() if k != "note"}

    changed_physics = [
        s.name
        for s in candidate.species()
        if s.name not in parent_species or physics(s.input_data) != physics(parent_species[s.name])
    ]
    removed = sorted(set(parent.species_names) - set(candidate.species_names))
    equations = set(parent.reaction_equations())
    parent_rates = {}
    for reaction in parent.reactions():
        parent_rates.setdefault(reaction.equation, []).append(reaction.rate.input_data)
    unmatched_rates = [
        i
        for i, reaction in enumerate(candidate.reactions())
        if not any(
            reaction.rate.input_data == rate for rate in parent_rates.get(reaction.equation, [])
        )
    ]
    return {
        "loadable": True,
        "native_guard": guard,
        "species": candidate.n_species,
        "reactions": candidate.n_reactions,
        "parent_species": parent.n_species,
        "parent_reactions": parent.n_reactions,
        "species_compression": 1 - candidate.n_species / parent.n_species,
        "reaction_compression": 1 - candidate.n_reactions / parent.n_reactions,
        "removed_species": removed,
        "changed_or_added_species_data": changed,
        "changed_or_added_species_physics": changed_physics,
        "species_note_only_changes": sorted(set(changed) - set(changed_physics)),
        "reaction_rate_indices_not_matched_to_parent": unmatched_rates,
        "new_equations": [e for e in candidate.reaction_equations() if e not in equations],
        "parent_warning_count": parent_notices,
        "candidate_warning_count": len(notices) - parent_notices,
        "accuracy_verified": False,
        "numerical_solves": 0,
        "boundary": "Native load/element guard only; not full chemistry or accuracy certification.",
    }


def audit(stage):
    stage = plain(stage)
    plan = json.loads((stage / "plan.json").read_text())
    cpu = json.loads(
        (
            Path(plan["root"]) / "local_cpu" / (plan.get("execution_id", plan["id"]) + ".json")
        ).read_text()
    )
    if cpu.get("status") != "SETTLED" or not (stage / "result.json").exists():
        raise PermissionError("actual execution must be terminal before submission audit")
    parent = stage / "task/parent.yaml"
    if file_sha(parent) != plan["task_manifest"]["parent.yaml"]:
        raise PermissionError("parent input changed")
    report = {
        "schema": "openworld-artifact-audit.v2",
        "stage": str(stage),
        "numerical_solves": 0,
        "benchmark_opened": False,
        "scientific_accuracy_verified": False,
        "candidates": [],
    }
    submission_path = stage / "submission.json"
    if not submission_path.exists():
        report["status"] = "NO_FINAL_SUBMISSION"
    else:
        submission = json.loads(submission_path.read_text())
        report["status"] = "INSPECTED_NOT_ACCURACY_CERTIFIED"
        for row in submission["mechanisms"] + [submission["report"]]:
            if Path(row["path"]).is_absolute() or ".." in Path(row["path"]).parts:
                raise PermissionError("foreign submitted path")
            path = plain(stage / "work" / row["path"])
            if not path.is_relative_to(stage / "work") or file_sha(path) != row["sha256"]:
                raise PermissionError("submitted file changed")
        for row in submission["mechanisms"]:
            item = {"path": row["path"], "sha256": row["sha256"]}
            try:
                item.update(audit_mechanism(parent, stage / "work" / row["path"]))
            except Exception as exc:
                item.update(loadable=False, error_type=type(exc).__name__, error=str(exc)[:1000])
            report["candidates"].append(item)
    atomic(stage / "independent_artifact_audit_v2.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    print(json.dumps(audit(parser.parse_args().stage), allow_nan=False))
