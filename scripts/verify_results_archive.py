"""Read-only checks of distributed bytes and recorded independent endpoint scores.

Uses the unchanged project scoring code, never a model, solver or network.
Self-contained hashes demonstrate export consistency, not historical authenticity.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_file(root, relative):
    root = root.resolve()
    parts = PurePosixPath(relative)
    if (not relative or relative == "." or parts.is_absolute() or ".." in parts.parts
            or "\\" in relative or str(parts) != relative):
        raise ValueError("Unsafe or noncanonical archive path")
    path = root / relative
    if path.resolve() != path or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("Missing/nonregular file or symlink in archive")
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_same(actual, expected, where="value"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError("Mismatched mapping: " + where)
        for key in expected:
            assert_same(actual[key], expected[key], where + "/" + key)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError("Mismatched list: " + where)
        for i, (a, b) in enumerate(zip(actual, expected)):
            assert_same(a, b, f"{where}/{i}")
    elif type(expected) in (float, int):
        if (type(actual) not in (float, int) or not math.isfinite(actual)
                or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)):
            raise ValueError("Mismatched numeric value: " + where)
    elif actual != expected or type(actual) is not type(expected):
        raise ValueError("Mismatched value: " + where)


def verify_file_manifest(root, entries):
    seen = set()
    for row in entries:
        if row["path"] in seen:
            raise ValueError("Duplicate manifest path")
        seen.add(row["path"])
        path = safe_file(root, row["path"])
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("File differs from manifest: " + row["path"])
    return len(seen)


def verify_endpoints(root):
    root = root.resolve()
    module_path = safe_file(root, "src/kinetic_agents/evaluation/scoring.py")
    spec = importlib.util.spec_from_file_location("result_archive_scoring", module_path)
    scoring = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scoring)
    pool = read(safe_file(root, "data/benchmark/evaluation_base.json"))
    scoring.validate_pool(pool)
    run_index = read(safe_file(root, "results/runs.json"))
    summaries = []

    def verified_endpoint(relative):
        directory = root / relative
        manifest = read(safe_file(root, relative + "/manifest.json"))
        result = read(safe_file(root, relative + "/evaluation_result.json"))
        card = read(safe_file(root, relative + "/scorecard.json"))
        for split in ("development", "recheck"):
            if manifest[split] != pool[split]:
                raise ValueError("Endpoint case content differs from supplied pool")
        if manifest["source_pins"] != pool["source_pins"]:
            raise ValueError("Scientific source pins differ from supplied pool")
        if result["manifest_sha256"] != scoring.fingerprint(manifest):
            raise ValueError("Canonical endpoint manifest identity mismatch")
        if result["contract_sha256"] != scoring.fingerprint(read(directory / "evaluation_contract.json")):
            raise ValueError("Canonical endpoint contract identity mismatch")
        if card["result_sha256"] != digest(directory / "evaluation_result.json"):
            raise ValueError("Byte-level scorecard/result identity mismatch")
        computed = scoring.summarize(manifest, result["rows"])
        assert_same(result["scores"], computed, relative + "/result_scores")
        assert_same(card["scores"], computed, relative + "/scorecard_scores")
        return manifest, result, card

    for run in run_index:
        run_id, arm = run["run_id"], run["arm_directory"]
        if arm is None:
            continue
        endpoint_rel = f"runs/{run_id}/{arm}/endpoint"
        if not (root / endpoint_rel).exists():
            if run["endpoint_status_metadata"] == "COMPLETE":
                raise ValueError("Completed endpoint metadata without supplied evidence")
            continue
        endpoint = root / endpoint_rel
        manifest, result, scorecard = verified_endpoint(endpoint_rel)
        mechanism_roots = {label: endpoint for label in manifest["selected"]}
        result_path, scorecard_path = endpoint_rel + "/evaluation_result.json", endpoint_rel + "/scorecard.json"
        repair_rel = f"runs/{run_id}/{arm}/endpoint_bulk_v1"
        repair_path = root / repair_rel
        source_attempts = [endpoint_rel]
        status = result["status"]
        if repair_path.exists():
            repair_manifest, repair_result, repair_card = verified_endpoint(repair_rel)
            if set(manifest["selected"]) & set(repair_manifest["selected"]):
                raise ValueError("Repair unexpectedly repeats candidate selection")
            merged = read(safe_file(root, repair_rel + "/combined_manifest.json"))
            combined = read(safe_file(root, repair_rel + "/combined_result.json"))
            combined_card = read(safe_file(root, repair_rel + "/combined_scorecard.json"))
            expected = {**repair_manifest, "selected": {**manifest["selected"], **repair_manifest["selected"]},
                        "endpoint_pairs": 610 * (len(manifest["selected"]) + len(repair_manifest["selected"]))}
            if merged != expected or combined["rows"] != result["rows"] + repair_result["rows"]:
                raise ValueError("Combined result does not preserve original+repair inputs")
            expected_sources = [
                {"attempt": "original", "result_sha256": digest(endpoint / "evaluation_result.json"), "labels": ["parent"]},
                {"attempt": "endpoint_bulk_v1", "result_sha256": digest(repair_path / "evaluation_result.json"), "labels": list(repair_manifest["selected"])}]
            if combined["row_sources"] != expected_sources or combined_card["row_sources"] != expected_sources:
                raise ValueError("Combined source lineage mismatch")
            if combined_card["combined_result_sha256"] != digest(repair_path / "combined_result.json"):
                raise ValueError("Combined scorecard hash mismatch")
            manifest, result, scorecard = merged, combined, combined_card
            status = repair_result["status"]
            mechanism_roots.update({label: repair_path for label in repair_manifest["selected"]})
            result_path, scorecard_path = repair_rel + "/combined_result.json", repair_rel + "/combined_scorecard.json"
            source_attempts.append(repair_rel)
        computed = scoring.summarize(manifest, result["rows"])
        assert_same(result["scores"], computed, run_id + "/chosen_result_scores")
        assert_same(scorecard["scores"], computed, run_id + "/chosen_scorecard_scores")
        submission = read(safe_file(root, f"runs/{run_id}/{arm}/submission.json"))
        submitted = {obj["sha256"] for obj in submission["mechanisms"]}
        candidate_rows = []
        for label, selected in manifest["selected"].items():
            mechanism = safe_file(mechanism_roots[label], selected["path"])
            if digest(mechanism) != selected["sha256"]:
                raise ValueError("Selected mechanism hash mismatch")
            if label != "parent" and selected["sha256"] not in submitted:
                raise ValueError("Evaluated mechanism is absent from locked submission")
            if label == "parent" and digest(mechanism) != digest(root / "tasks/usc_ii/parent.yaml"):
                raise ValueError("Parent mechanism differs")
            panel = computed[label]["all610"]
            candidate_rows.append({"label": label, "sha256": selected["sha256"],
                                   "mechanism": mechanism.relative_to(root).as_posix(),
                                   "species": selected["species_count"], "reactions": selected["reaction_count"],
                                   **panel})
        summaries.append({"run_id": run_id, "task_variant": run["task_variant"],
                          "profile": run["profile"], "model": run["model"], "harness": run["harness"],
                          "status": status, "evaluation_result": result_path,
                          "scorecard": scorecard_path, "source_attempts_preserved": source_attempts,
                          "rows_including_parent": len(result["rows"]),
                          "candidates_including_parent": candidate_rows})
    return {"scope": "Existing captured endpoint results, separate from dated report; not new solver evidence or a global ranking",
            "endpoint_count": len(summaries), "endpoints": summaries,
            "model_calls": 0, "solver_calls": 0, "network_calls": 0}


def verify(root):
    root = root.resolve()
    manifest = read(safe_file(root, "results/files.json"))
    count = verify_file_manifest(root, manifest["files"])
    endpoints = verify_endpoints(root)
    assert_same(read(safe_file(root, "results/endpoint_summary.json")), endpoints, "endpoint_summary")
    return {"status": "verified", "manifest_files": count,
            "manifest_self_excluded": "results/files.json",
            "endpoint_count": endpoints["endpoint_count"],
            "candidate_count_excluding_parent": sum(len(e["candidates_including_parent"]) - 1 for e in endpoints["endpoints"]),
            "recorded_rows_including_parent": sum(e["rows_including_parent"] for e in endpoints["endpoints"]),
            "model_calls": 0, "solver_calls": 0, "network_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    print(json.dumps(verify(parser.parse_args().root), ensure_ascii=False, indent=2))
