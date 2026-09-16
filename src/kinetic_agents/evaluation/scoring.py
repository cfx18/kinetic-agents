"""Extracted reusable implementation; historical launchers intentionally excluded."""

from collections import Counter
import hashlib, math, json


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_pool(pool):
    ids = {}
    for split, expected in [("development", 491), ("recheck", 119)]:
        rows = pool[split]
        keys = [r["case_id"] for r in rows]
        if len(rows) != expected or len(set(keys)) != expected:
            raise ValueError("pool count or duplicate case mismatch")
        ids[split] = set(keys)
    if ids["development"] & ids["recheck"]:
        raise ValueError("overlapping splits")
    cases = pool["development"] + pool["recheck"]
    if Counter(c["operator"]["family"] for c in cases) != {
        "ignition_delay": 320,
        "laminar_flame_speed": 290,
    }:
        raise ValueError("observable composition changed")
    return cases


def panel_score(cases, rows):
    expected = {c["case_id"] for c in cases}
    if len(expected) != len(cases):
        raise ValueError("duplicate requested case")
    values = {}
    for row in rows:
        cid = row["case_id"]
        if cid not in expected or cid in values:
            raise ValueError("foreign or duplicate result")
        if row["status"] == "success":
            score = row.get("signed_sigma")
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError("success requires finite signed_sigma")
        values[cid] = row
    successful = [abs(r["signed_sigma"]) for r in rows if r["status"] == "success"]
    mean = sum(successful) / len(successful) if successful else None
    return {
        "case_count": len(cases),
        "recorded": len(rows),
        "success": len(successful),
        "coverage": len(successful) / len(cases) if cases else None,
        "missing": len(cases) - len(rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "full_pool_mean_abs_sigma": mean if cases and len(successful) == len(cases) else None,
        "successful_subset_mean_abs_sigma": mean,
    }


def summarize(manifest, rows):
    cases = validate_pool(manifest)
    selected = manifest["selected"]
    if any(r.get("label") not in selected for r in rows):
        raise ValueError("foreign candidate label")
    result = {}
    for label, candidate in selected.items():
        own = [r for r in rows if r["label"] == label]
        if any(r.get("candidate_id") != candidate["id"] for r in own):
            raise ValueError("candidate identity mismatch")
        result[label] = {"all610": panel_score(cases, own)}
        for split in ("development", "recheck"):
            ids = {c["case_id"] for c in manifest[split]}
            result[label][split] = panel_score(
                manifest[split], [r for r in own if r["case_id"] in ids]
            )
        groups = {}
        for fuel, family in sorted({(c["fuel_label"], c["operator"]["family"]) for c in cases}):
            subset = [
                c for c in cases if (c["fuel_label"], c["operator"]["family"]) == (fuel, family)
            ]
            ids = {c["case_id"] for c in subset}
            groups[fuel + "/" + family] = panel_score(
                subset, [r for r in own if r["case_id"] in ids]
            )
        result[label]["fuel_observable_groups"] = groups
    return result
