"""Synthetic only: no model, credential, historical score or solver access."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from kinetic_agents.evaluation.rubric import DIMENSIONS, validate_review
from kinetic_agents.evaluation.rubric.packet import build_packet, read_spec, load_packet, load_document
from kinetic_agents.evaluation.rubric.service import EvidenceService, check_response

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text("A synthetic report.\nAn observed failure is disclosed.\n")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"comparison": "Established method; no novelty claimed.", "api_key": "synthetic-secret", "reasoning": "private field"}))
    spec = read_spec(PROJECT / "configs/rubric.yaml")
    result = build_packet(tmp_path / "packet", spec,
                          [("submitted_report", "report", report), ("public_source", "literature", source)])
    assert result["model_calls"] == 0 and not result["live_execution_enabled"]
    return load_packet(tmp_path / "packet")


def response(packet):
    result = dict(schema_version="research-rubric-output.v2", packet_id=packet["packet_id"],
                summary_zh="仅为合成测试，不是真实科学评分。", critical_findings_zh=[],
                decision_audit=[],
                dimensions={name: dict(score=2, status="rated", confidence="low", rationale_zh="合成证据支持测试评分。",
                    evidence=[dict(document_id="d0002" if name == "Novelty" else "d0001",
                                   start_line=1, end_line=4 if name == "Novelty" else 1,
                                   quote="Established method; no novelty claimed." if name == "Novelty" else "A synthetic report.")],
                    limitations_zh=["需人工核验。"], next_checks_zh=["检查外部证据。"])
                    for name in DIMENSIONS})
    result["dimensions"]["DecisionQuality"].update(score=None, status="insufficient_evidence", evidence=[],
        limitations_zh=["缺少当时的决策记录和独立评测，不从报告推测。"])
    return result


def test_spec_is_six_dimensional_english_draft():
    spec = read_spec(PROJECT / "configs/rubric.yaml")
    assert list(spec["dimensions"]) == list(DIMENSIONS)
    assert spec["aggregate"] == "none" and spec["status"] == "draft"
    assert spec["judge"]["model"] == "gpt-6-astra"
    assert len(DIMENSIONS) == 6
    assert spec["account"] == {"auth_home": "/root/.codex-experiment", "api_fallback": False}
    assert json.dumps(spec, ensure_ascii=False).isascii()


def test_prompt_instructions_are_english_and_account_is_host_only(bundle):
    prompt = (bundle["_directory"] / "prompt.md").read_text()
    assert prompt.isascii()
    assert "Chinese" in prompt
    assert "/root/.codex-experiment" not in prompt
    assert "DecisionQuality" in prompt and "per-case" in prompt


def test_no_account_fallback(tmp_path):
    import yaml
    spec = read_spec(PROJECT / "configs/rubric.yaml")
    spec["account"]["auth_home"] = "/root/.codex"
    path = tmp_path / "wrong-account.yaml"
    path.write_text(yaml.safe_dump(spec))
    with pytest.raises(ValueError, match="dedicated experiment"):
        read_spec(path)


def test_sanitized_packet_and_host_provenance(bundle):
    text = load_document(bundle, "d0002")
    assert "synthetic-secret" not in text and "private field" not in text
    assert "REDACTED" in text
    assert "source_sha256" not in bundle["documents"]["d0002"]
    with pytest.raises(ValueError):
        load_document(bundle, "../../host-provenance.json")


def test_validate_and_na(bundle):
    row = response(bundle)
    assert validate_review(row, bundle) == row
    row["dimensions"]["Novelty"].update(score=None, status="insufficient_evidence", evidence=[])
    validate_review(row, bundle)


@pytest.mark.parametrize("value", [-1, 5, 2.5, True, "3", float("nan")])
def test_invalid_score(bundle, value):
    row = response(bundle)
    row["dimensions"]["Taste"]["score"] = value
    with pytest.raises(ValueError):
        validate_review(row, bundle)


def test_no_total_and_no_fake_citation(bundle):
    row = response(bundle)
    row["total_score"] = 90
    with pytest.raises(ValueError):
        validate_review(row, bundle)
    del row["total_score"]
    row["dimensions"]["Taste"]["evidence"][0]["quote"] = "unrecorded excellence"
    with pytest.raises(ValueError):
        validate_review(row, bundle)


def test_novelty_not_supported_by_self_claim_alone(bundle):
    row = response(bundle)
    row["dimensions"]["Novelty"]["evidence"] = deepcopy(row["dimensions"]["Taste"]["evidence"])
    with pytest.raises(ValueError, match="Novelty"):
        validate_review(row, bundle)


def test_na_is_not_zero(bundle):
    row = response(bundle)
    row["dimensions"]["Taste"].update(status="insufficient_evidence", score=0)
    with pytest.raises(ValueError):
        validate_review(row, bundle)


def test_service_actual_reads_and_live_audit(bundle):
    with EvidenceService(bundle["_directory"]) as service:
        row = response(bundle)
        with pytest.raises(ValueError, match="not retrieved"):
            service.submit(row)
        assert len(service.catalog()["documents"]) == 2
        hit = service.search(["synthetic"], limit=1)
        assert hit["total_matches"] >= 1
        for doc in bundle["documents"]:
            service.read(doc)
        receipt = service.submit(row)
        assert receipt["model_provenance"] == "unverified"
        assert not receipt["numerical_scores_modified"]
        assert (service.output / "transcript.jsonl").stat().st_size > 0
        assert (service.output / "REVIEW_ZH.md").exists()
        with pytest.raises(PermissionError):
            service.submit(row)


def test_catalog_paginated_not_full_history(bundle):
    with EvidenceService(bundle["_directory"]) as service:
        first = service.catalog(limit=1)
        assert len(first["documents"]) == 1 and first["next_offset"] == 1
        second = service.catalog(offset=1, limit=1)
        assert len(second["documents"]) == 1 and second["next_offset"] is None
        assert set(first["documents"]).isdisjoint(second["documents"])
        assert service.catalog(kind="public_source")["matching_documents"] == 1


def test_packet_tamper_and_symlinks(bundle, tmp_path):
    path = bundle["_directory"] / "documents/d0001.txt"
    path.write_text("changed")
    with pytest.raises(PermissionError):
        load_document(bundle, "d0001")
    source = tmp_path / "linked.txt"
    source.symlink_to(path)
    with pytest.raises(PermissionError):
        build_packet(tmp_path / "bad", bundle["rubric"], [("public_source", "bad", source)])


def test_existing_packet_cannot_be_overwritten(bundle):
    with pytest.raises(FileExistsError):
        build_packet(bundle["_directory"], bundle["rubric"], [])


def test_offline_check_is_not_claimed_as_model_review(bundle, tmp_path):
    path = tmp_path / "response.json"
    path.write_text(json.dumps(response(bundle)))
    receipt = check_response(bundle["_directory"], path)
    assert receipt["model_calls"] == 0 and receipt["model_provenance"] == "unverified_import"


def test_oversize_mandatory_evidence_fails(tmp_path, monkeypatch):
    from kinetic_agents.evaluation.rubric import packet as module
    monkeypatch.setattr(module, "MAX_TOTAL", 1)
    path = tmp_path / "test.txt"
    path.write_text("longer than bound")
    with pytest.raises(ValueError):
        build_packet(tmp_path / "packet", {}, [("task", "task", path)])


def test_cli_offline_check(bundle, tmp_path, capsys):
    from kinetic_agents.cli import main
    path = tmp_path / "response.json"
    path.write_text(json.dumps(response(bundle)))
    assert main(["rubric-check", "--run", str(bundle["_directory"]), "--response", str(path)]) == 0
    assert '"model_calls": 0' in capsys.readouterr().out


def prepared_run_fixture(tmp_path, monkeypatch):
    from kinetic_agents import runner
    from kinetic_agents.evaluation.rubric.packet import digest
    run = tmp_path / "run"
    root = run / "solo-max"
    task = tmp_path / "task"
    task.mkdir()
    (task / "TASK.md").write_text("Synthetic scientific task")
    root.mkdir(parents=True)
    (root / "input-reference.json").write_text(json.dumps(dict(schema="shared-task.v1", task_directory=str(task))))
    (root / "local_cpu.json").write_text(json.dumps(dict(status="SETTLED")))
    (root / "remote_status.json").write_text(json.dumps(dict(jobs=[])))
    final = root / "final_artifacts"
    final.mkdir()
    report = b"Synthetic submission report"
    mechanism = b"Synthetic mechanism; no chemistry executed"
    items = []
    for data in (report, mechanism):
        ident = digest(data)
        (final / ident).write_bytes(data)
        items.append(dict(sha256=ident, path=ident, bytes=len(data)))
    submission = dict(report=items[0], mechanisms=items[1:])
    (root / "result.json").write_text(json.dumps(dict(submission=submission)))
    (root / "endpoint").mkdir()
    raw = json.dumps(dict(rows=[dict(label="parent", case_id="synthetic-1", status="success", prediction=1.0)])).encode()
    (root / "endpoint/evaluation_result.json").write_bytes(raw)
    (root / "endpoint/scorecard.json").write_text(json.dumps(dict(synthetic=True, scores={"parent": {}}, result_sha256=digest(raw))))
    monkeypatch.setattr(runner, "load", lambda _: (root, {"task_sha256": digest((task / "TASK.md").read_bytes())}, {}))
    return run, root


def test_prepare_ended_run_captures_locked_files_only(tmp_path, monkeypatch):
    from kinetic_agents.evaluation.rubric.packet import prepare_run
    run, root = prepared_run_fixture(tmp_path, monkeypatch)
    (root / "auth-host").mkdir()
    (root / "auth-host/secret.txt").write_text("unrelated private canary")
    result = prepare_run(run, PROJECT / "configs/rubric.yaml")
    packet = load_packet(result["packet"])
    labels = {v["label"] for v in packet["documents"].values()}
    assert {"independent_endpoint_scores", "independent_endpoint_per_case"} <= labels
    assert all("private canary" not in load_document(packet, key) for key in packet["documents"])
    assert result["model_calls"] == 0
    with EvidenceService(result["packet"]) as service:
        with pytest.raises(ValueError, match="required task/evaluation"):
            service.submit({})
        # Catalog/search discovery alone is not a successful evidence read.
        service.catalog()
        service.search(["Synthetic"])
        with pytest.raises(ValueError, match="required task/evaluation"):
            service.submit({})


@pytest.mark.parametrize("failure", ["running", "endpoint_missing", "tampered_submission", "wrong_endpoint", "raw_missing", "raw_changed"])
def test_prepare_does_not_grade_incomplete_or_mutable_results(tmp_path, monkeypatch, failure):
    from kinetic_agents.evaluation.rubric.packet import prepare_run
    run, root = prepared_run_fixture(tmp_path, monkeypatch)
    if failure == "running":
        (root / "local_cpu.json").write_text(json.dumps(dict(status="ACTIVE")))
    elif failure == "endpoint_missing":
        (root / "endpoint/scorecard.json").unlink()
    elif failure == "tampered_submission":
        next((root / "final_artifacts").iterdir()).write_text("tampered")
    elif failure == "raw_missing":
        (root / "endpoint/evaluation_result.json").unlink()
    elif failure == "raw_changed":
        (root / "endpoint/evaluation_result.json").write_text('{"rows": [{"fabricated": true}]}')
    with pytest.raises((PermissionError, FileNotFoundError, ValueError)):
        prepare_run(run, PROJECT / "configs/rubric.yaml",
                    endpoint="../../other/scorecard.json" if failure == "wrong_endpoint" else "endpoint/scorecard.json")
    assert not (run / "rubric").exists()


def test_decision_quality_cannot_be_inferred_from_report(bundle):
    row = response(bundle)
    row["dimensions"]["DecisionQuality"] = deepcopy(row["dimensions"]["Taste"])
    with pytest.raises(ValueError, match="DecisionQuality requires"):
        validate_review(row, bundle)


def test_decision_audit_links_actions_and_actual_outcomes(bundle, tmp_path):
    records = []
    for kind, label, content in [("submitted_report", "report", "A synthetic report."),
                                ("public_source", "literature", "Established method; no novelty claimed."),
                                ("decision_record", "decision", "Observed group failure; chose a recovery test."),
                                ("evaluator_output", "endpoint", "Independent evaluation: 3 successes, 1 failure.")]:
        path = tmp_path / (label + ".txt")
        path.write_text(content)
        records.append((kind, label, path))
    build_packet(tmp_path / "audit-packet", bundle["rubric"], records)
    packet = load_packet(tmp_path / "audit-packet")
    by_kind = {row["kind"]: ident for ident, row in packet["documents"].items()}
    def ref(kind):
        ident = by_kind[kind]
        return dict(document_id=ident, start_line=1, end_line=1, quote=load_document(packet, ident))
    row = response(packet)
    for name, dimension in row["dimensions"].items():
        dimension["evidence"] = [ref("public_source" if name == "Novelty" else "submitted_report")]
    dq = row["dimensions"]["DecisionQuality"]
    dq.update(score=2, status="rated", evidence=[ref("decision_record"), ref("evaluator_output")])
    with pytest.raises(ValueError, match="traceable decision audit"):
        validate_review(row, packet)
    row["decision_audit"] = [dict(decision_zh="恢复实验。", information_available_then_zh="分组失败。",
        action_and_alternatives_zh="先测试恢复，不盲目继续删减。", feedback_and_adaptation_zh="当前片段未记录后续反馈。",
        efficiency_zh="缺少成本，不确定效率。", independent_outcome_zh="独立评测仍有一次失败。",
        hindsight_limit_zh="独立评测不在当时可见，不能倒推其已知。",
        decision_refs=[ref("decision_record")], feedback_refs=[], outcome_refs=[ref("evaluator_output")])]
    assert validate_review(row, packet) == row
    with EvidenceService(packet["_directory"]) as service:
        with pytest.raises(ValueError, match="not retrieved"):
            service.submit(row)
        for ident in packet["documents"]:
            service.read(ident)
        service.submit(row)
        assert "避免事后归因" in (service.output / "REVIEW_ZH.md").read_text()


def test_legacy_five_dimension_review_still_readable(bundle):
    legacy = {**bundle, "rubric": {**bundle["rubric"], "version": "research-rubric.v1"}}
    row = response(legacy)
    row["schema_version"] = "research-rubric-output.v1"
    del row["dimensions"]["DecisionQuality"], row["decision_audit"]
    validate_review(row, legacy)


def test_repaired_endpoint_includes_original_parent_reference(tmp_path, monkeypatch):
    from kinetic_agents.evaluation.rubric.packet import prepare_run, digest
    run, root = prepared_run_fixture(tmp_path, monkeypatch)
    repaired = root / "endpoint_bulk_v1"
    repaired.mkdir()
    raw = json.dumps(dict(rows=[dict(label="candidate", case_id="synthetic-1", status="success", prediction=1.1)])).encode()
    (repaired / "evaluation_result.json").write_bytes(raw)
    (repaired / "scorecard.json").write_text(json.dumps(dict(scores={"candidate": {}}, result_sha256=digest(raw))))
    result = prepare_run(run, PROJECT / "configs/rubric.yaml", endpoint="endpoint_bulk_v1/scorecard.json")
    packet = load_packet(result["packet"])
    labels = {row["label"] for row in packet["documents"].values()}
    assert {"independent_endpoint_scores", "independent_endpoint_per_case", "parent_reference_scores_original_endpoint",
            "parent_reference_per_case_original_endpoint"} <= labels
