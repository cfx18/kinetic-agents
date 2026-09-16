"""Strict model-output contract. Reference validity is not semantic correctness."""

DIMENSIONS = ("Taste", "Methodology", "Novelty", "Solid", "Robust", "DecisionQuality")


def obj(properties):
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


def output_schema():
    text = {"type": "string", "minLength": 1}
    texts = {"type": "array", "items": text}
    citation = obj({"document_id": text, "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1}, "quote": text})
    dimension = obj({
        "score": {"type": ["integer", "null"], "enum": [None, 0, 1, 2, 3, 4]},
        "status": {"type": "string", "enum": ["rated", "insufficient_evidence"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale_zh": text, "evidence": {"type": "array", "items": citation},
        "limitations_zh": texts, "next_checks_zh": texts,
    })
    refs = {"type": "array", "items": citation}
    audit = obj({"decision_zh": text, "information_available_then_zh": text,
                 "action_and_alternatives_zh": text, "feedback_and_adaptation_zh": text,
                 "efficiency_zh": text, "independent_outcome_zh": text, "hindsight_limit_zh": text,
                 "decision_refs": refs, "feedback_refs": refs, "outcome_refs": refs})
    return obj({"schema_version": {"const": "research-rubric-output.v2"},
                "packet_id": text, "summary_zh": text,
                "dimensions": obj({key: dimension for key in DIMENSIONS}),
                "decision_audit": {"type": "array", "items": audit, "maxItems": 20},
                "critical_findings_zh": texts})


def validate_review(value, packet, *, read_ranges=None):
    """No fabricated score for NA, no extra total, citations quote frozen bytes.

    read_ranges optionally binds citations to successful host-tool reads. Without
    it, validation only certifies packet references, not model provenance.
    """
    from .packet import load_document

    def keys(x, expected):
        if not isinstance(x, dict) or set(x) != set(expected):
            raise ValueError("rubric output has missing or unexpected fields")

    def string(x):
        if not isinstance(x, str) or not x.strip() or len(x) > 12000:
            raise ValueError("bounded nonempty rubric text required")

    def strings(x):
        if not isinstance(x, list) or len(x) > 20:
            raise ValueError("bounded rubric text list required")
        for item in x:
            string(item)

    legacy = packet["rubric"].get("version") == "research-rubric.v1"
    dimensions = DIMENSIONS[:-1] if legacy else DIMENSIONS
    keys(value, ("schema_version", "packet_id", "summary_zh", "dimensions", "critical_findings_zh") + (() if legacy else ("decision_audit",)))
    version = "research-rubric-output.v1" if legacy else "research-rubric-output.v2"
    if value["schema_version"] != version or value["packet_id"] != packet["packet_id"]:
        raise ValueError("rubric packet/output identity mismatch")
    string(value["summary_zh"])
    strings(value["critical_findings_zh"])
    def citations(refs):
        if not isinstance(refs, list) or len(refs) > 20:
            raise ValueError("bounded citation list required")
        for ref in refs:
            keys(ref, ("document_id", "start_line", "end_line", "quote"))
            string(ref["quote"])
            start, end = ref["start_line"], ref["end_line"]
            if type(start) is not int or type(end) is not int or not 1 <= start <= end or end - start >= 80:
                raise ValueError("invalid citation line range")
            lines = load_document(packet, ref["document_id"]).splitlines()
            if end > len(lines) or ref["quote"] not in "\n".join(lines[start - 1:end]):
                raise ValueError("citation quote does not match frozen evidence")
            if read_ranges is not None and not any(a <= start and end <= b for a, b in read_ranges.get(ref["document_id"], [])):
                raise ValueError("citation was not retrieved in this judge session")
        return {packet["documents"][ref["document_id"]]["kind"] for ref in refs}

    keys(value["dimensions"], dimensions)
    for name, row in value["dimensions"].items():
        keys(row, ("score", "status", "confidence", "rationale_zh", "evidence", "limitations_zh", "next_checks_zh"))
        if row["status"] not in ("rated", "insufficient_evidence"):
            raise ValueError("unknown dimension status")
        if row["confidence"] not in ("low", "medium", "high"):
            raise ValueError("confidence is ordinal, not a probability")
        if row["status"] == "insufficient_evidence":
            if row["score"] is not None or row["confidence"] != "low" or not row["limitations_zh"]:
                raise ValueError("NA requires null score, low confidence and explicit evidence gap")
        elif type(row["score"]) is not int or not 0 <= row["score"] <= 4:
            raise ValueError("rated score must be an integer in 0..4")
        string(row["rationale_zh"])
        strings(row["limitations_zh"])
        strings(row["next_checks_zh"])
        if not isinstance(row["evidence"], list) or len(row["evidence"]) > 20:
            raise ValueError("bounded citation list required")
        if row["status"] == "rated" and not row["evidence"]:
            raise ValueError("rated dimension requires evidence")
        classes = citations(row["evidence"])
        if name == "Novelty" and row["status"] == "rated":
            classes = {packet["documents"][r["document_id"]]["kind"] for r in row["evidence"]}
            if "public_source" not in classes and "baseline_evidence" not in classes:
                raise ValueError("Novelty needs retrieved literature/baseline evidence, not self-claims alone")
        if name == "DecisionQuality" and row["status"] == "rated" and not {"decision_record", "evaluator_output"} <= classes:
            raise ValueError("DecisionQuality requires decision records AND actual independent evaluation evidence")
    if not legacy:
        audits = value["decision_audit"]
        if not isinstance(audits, list) or len(audits) > 20:
            raise ValueError("bounded decision audit list required")
        if value["dimensions"]["DecisionQuality"]["status"] == "rated" and not audits:
            raise ValueError("rated DecisionQuality requires a traceable decision audit")
        fields = ("decision_zh", "information_available_then_zh", "action_and_alternatives_zh",
                  "feedback_and_adaptation_zh", "efficiency_zh", "independent_outcome_zh", "hindsight_limit_zh")
        for audit in audits:
            keys(audit, fields + ("decision_refs", "feedback_refs", "outcome_refs"))
            for field in fields:
                string(audit[field])
            if "decision_record" not in citations(audit["decision_refs"]):
                raise ValueError("decision audit needs contemporaneous decision evidence")
            citations(audit["feedback_refs"])
            if "evaluator_output" not in citations(audit["outcome_refs"]):
                raise ValueError("decision audit needs independent outcome evidence")
    return value


def markdown(value):
    out = ["# 科研质量辅助评审", "", "探索性模型评审；不替代数值评分，不是人工专家认证。", "",
           value["summary_zh"], "", "| 维度 | 分数（0–4） | 证据置信度 |", "|---|---:|---|"]
    for name in value["dimensions"]:
        row = value["dimensions"][name]
        out.append(f'| {name} | {row["score"] if row["score"] is not None else "NA"} | {row["confidence"]} |')
    for name in value["dimensions"]:
        row = value["dimensions"][name]
        out.extend(["", f"## {name}", "", row["rationale_zh"], ""])
        for ref in row["evidence"]:
            out.append(f'- 证据 `{ref["document_id"]}` 第{ref["start_line"]}–{ref["end_line"]}行：{ref["quote"]}')
        for label, key in [("局限", "limitations_zh"), ("下一检查", "next_checks_zh")]:
            out.extend(["", f"{label}：", ""] + ["- " + x for x in row[key]])
    for audit in value.get("decision_audit", []):
        out.extend(["", "## 决策复盘：" + audit["decision_zh"], ""])
        for label, key in [("当时信息", "information_available_then_zh"), ("行动与替代", "action_and_alternatives_zh"),
                           ("反馈与调整", "feedback_and_adaptation_zh"), ("效率", "efficiency_zh"),
                           ("独立评测结果", "independent_outcome_zh"), ("避免事后归因", "hindsight_limit_zh")]:
            out.extend([f"{label}：{audit[key]}", ""])
        for refs in ("decision_refs", "feedback_refs", "outcome_refs"):
            for ref in audit[refs]:
                out.append(f'- {refs}: `{ref["document_id"]}` 第{ref["start_line"]}–{ref["end_line"]}行：{ref["quote"]}')
    out.extend(["", "## 关键问题", ""] + ["- " + x for x in value["critical_findings_zh"]])
    return "\n".join(out) + "\n"
