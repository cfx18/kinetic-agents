"""Chinese decision cards for expert review, with version-bound feedback.

Interpretation is explicitly supplied by a curator; it is not synthesized from
tool names. No LLM, scientific execution, label inference or training happens.
"""

import hashlib
import json
from pathlib import Path
import re
import time

from kinetic_agents.core.storage import atomic
from kinetic_agents.core.storage import locked

SCHEMA = "expert-decision-cards.v1"
FIELDS = (
    "id",
    "title",
    "stage",
    "actor",
    "when",
    "known_then",
    "reason_summary",
    "reason_basis",
    "decision",
    "action",
    "outcome",
    "assessment",
    "uncertainty",
    "question",
    "priority",
    "sources",
)
VERDICTS = ("reasonable", "unreasonable", "insufficient_evidence", "mixed")
LAYERS = (
    "task",
    "case_selection",
    "method",
    "tool_implementation",
    "validation",
    "infrastructure",
    "stopping",
    "memory",
    "delegation",
    "reporting",
    "none",
)
QUERY_FIELDS = (
    "id",
    "cards",
    "purpose",
    "method",
    "request",
    "returned",
    "agent_takeaway",
    "used_in",
    "takeaway_basis",
    "sources",
)


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def validate(bundle, report):
    if not isinstance(bundle, dict) or set(bundle) != {
        "schema",
        "contract_hash",
        "title",
        "overview",
        "cards",
        "queries",
    }:
        raise ValueError("explicit expert card bundle required")
    if (
        bundle["schema"] != SCHEMA
        or not isinstance(bundle["contract_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", bundle["contract_hash"])
        or bundle["contract_hash"] != report["search"]["state"].get("contract_hash")
    ):
        raise PermissionError("expert cards belong to a different run or schema")
    if not isinstance(bundle["cards"], list) or not 1 <= len(bundle["cards"]) <= 40:
        raise ValueError(
            "review packet needs 1..40 material decisions; raw actions stay in the appendix"
        )
    for key in ("title", "overview"):
        if not isinstance(bundle[key], str) or not 1 <= len(bundle[key]) <= 2000:
            raise ValueError("short explicit Chinese-facing title and overview required")
    known = {
        f"{r['source']}:{r['line']}"
        for field in ("actions", "statements")
        for r in report["search"]["native"][field]
    }
    ids = set()
    for card in bundle["cards"]:
        if not isinstance(card, dict) or set(card) != set(FIELDS):
            raise ValueError("every card must expose the same review fields")
        if (
            not isinstance(card["id"], str)
            or not re.fullmatch(r"[ST][0-9]{2}", card["id"])
            or card["id"] in ids
        ):
            raise ValueError("unique stable card ID required")
        ids.add(card["id"])
        if card["priority"] not in ("high", "normal") or card["reason_basis"] not in (
            "public_statement",
            "unknown",
        ):
            raise ValueError("explicit priority and rationale provenance required")
        for key in set(FIELDS) - {"sources", "priority", "reason_basis", "id"}:
            if not isinstance(card[key], str) or not card[key].strip() or len(card[key]) > 1600:
                raise ValueError("short populated review fields required")
        if not isinstance(card["sources"], list) or not card["sources"]:
            raise ValueError("evidence links required; no invented rationale")
        # Native addresses must resolve to actual exported actions/statements.
        for source in card["sources"]:
            if (
                not isinstance(source, str)
                or not 1 <= len(source) <= 500
                or Path(source).is_absolute()
                or ".." in Path(source).parts
            ):
                raise ValueError("bounded evidence address required")
            if source.startswith("native/") and source not in known:
                raise ValueError("native evidence address does not exist")
        if card["reason_basis"] == "public_statement" and not any(
            f"{r['source']}:{r['line']}" in card["sources"]
            for r in report["search"]["native"]["statements"]
        ):
            raise ValueError(
                "translated reasoning summary requires an actual public statement reference"
            )
    if not isinstance(bundle["queries"], list) or len(bundle["queries"]) > 100:
        raise ValueError("bounded curated query records required")
    outputs = {
        f"{r['output_source']['source']}:{r['output_source']['line']}"
        for r in report["search"]["native"]["actions"]
        if r.get("output_source")
    }
    statements = {f"{r['source']}:{r['line']}" for r in report["search"]["native"]["statements"]}
    query_ids = set()
    for query in bundle["queries"]:
        if not isinstance(query, dict) or set(query) != set(QUERY_FIELDS):
            raise ValueError("query must distinguish retrieval, return, importance and use")
        if (
            not isinstance(query["id"], str)
            or not re.fullmatch(r"[ST]Q[0-9]{2}", query["id"])
            or query["id"] in query_ids
        ):
            raise ValueError("unique query identity required")
        query_ids.add(query["id"])
        if (
            not isinstance(query["cards"], list)
            or not query["cards"]
            or any(not isinstance(c, str) or c not in ids for c in query["cards"])
        ):
            raise ValueError("query must link to existing decision cards")
        for key in ("purpose", "method", "request", "returned", "agent_takeaway", "used_in"):
            if not isinstance(query[key], str) or not 1 <= len(query[key]) <= 2000:
                raise ValueError("bounded query fields required")
        if query["takeaway_basis"] not in ("public_statement", "unknown"):
            raise ValueError("importance is not inferred from returned content")
        if (
            not isinstance(query["sources"], list)
            or not query["sources"]
            or any(not isinstance(s, str) or s not in known | outputs for s in query["sources"])
            or not set(query["sources"]) & outputs
        ):
            raise ValueError("query requires existing native actions and actual return evidence")
        if query["takeaway_basis"] == "public_statement" and not set(query["sources"]) & statements:
            raise ValueError("claimed importance requires a public statement source")
    return bundle


def render(bundle, report):
    """Expert-first main page: no commands, UUIDs, English logs or JSON blocks."""
    if bundle is None:
        return (
            "# 专家决策复盘：待中文整理\n\n"
            "当前仅完成原始记录收集，尚未生成可供专家快速审阅的中文决策卡。\n\n"
            "不能把工具日志当成已经完成的专家复盘。下一步需逐项填写：当时信息、判断、决策、实际动作、结果、待审问题。\n\n"
            "[按步骤查看全部可观察操作](STEP_BY_STEP.md)。\n\n"
            "[技术附录](TECHNICAL_AUDIT.md)仅供溯源，不是专家首页。\n"
        )
    validate(bundle, report)
    lines = [
        "# " + bundle["title"],
        "",
        "[按步骤查看全部执行（含等待、报错、重试）](STEP_BY_STEP.md)",
        "",
        bundle["overview"],
        "",
        "## 先看这些节点",
        "",
        "|编号|发生了什么|请你判断什么|",
        "|---|---|---|",
    ]
    for card in bundle["cards"]:
        if card["priority"] == "high":
            lines.append(
                f"|[{card['id']}](#{card['id'].lower()})|{card['title']}|{card['question']}|"
            )
    lines += [
        "",
        "读法：判断是公开说明的中文译述，不是内部思维链；复盘意见是我们的事后分析，不是你的结论。",
        "下面每卡对应一个科研决策节点，多条读文件/运行命令合并到同一实际动作中。",
        "",
    ]
    for card in bundle["cards"]:
        lines += [
            f"<a id=\"{card['id'].lower()}\"></a>",
            "",
            f"## {card['id']}｜{card['title']}",
            "",
            f"{card['actor']} · {card['when']} · 环节：{card['stage']}",
            "",
            "|你要看的内容|这一步实际发生的事|",
            "|---|---|",
        ]
        labels = [
            ("known_then", "当时已知"),
            ("reason_summary", "Agent当时的判断"),
            ("decision", "作出的决策"),
            ("action", "实际动作"),
            ("outcome", "后来结果"),
            ("assessment", "初步复盘意见"),
            ("uncertainty", "还不能确定"),
            ("question", "请你判断"),
        ]
        for key, label in labels:
            text = card[key].replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"|{label}|{text}|")
        for query in bundle["queries"]:
            if card["id"] in query["cards"]:
                lines += [
                    "",
                    f"### {query['id']}｜这一步怎么查资料／结果",
                    "",
                    "|查询环节|记录|",
                    "|---|---|",
                ]
                for key, label in [
                    ("purpose", "要解决的问题"),
                    ("method", "在哪里、用什么查"),
                    ("request", "实际关键词／筛选条件"),
                    ("returned", "返回了什么"),
                    ("agent_takeaway", "Agent明确看重什么"),
                    ("used_in", "如何影响下一步"),
                ]:
                    text = query[key].replace("|", "\\|").replace("\n", "<br>")
                    lines.append(f"|{label}|{text}|")
        lines += [
            "",
            f"你的反馈：**{card['id']}：合理／不合理／信息不足／部分合理；哪一步有问题；应该怎么做；适用条件。**",
            "",
            "<details><summary>需要时再展开：原始证据</summary>",
            "",
        ]
        sources = list(
            dict.fromkeys(
                card["sources"]
                + [s for q in bundle["queries"] if card["id"] in q["cards"] for s in q["sources"]]
            )
        )
        for i, source in enumerate(sources, 1):
            if source.startswith("native/"):
                url = str(Path(report["search"]["run_root"]) / source)
            elif source.startswith("endpoint/"):
                url = str(Path(report["search"]["run_root"]) / source)
            else:
                url = str(Path(__file__).resolve().parents[1] / source)
            lines.append(f"- [证据{i}]({url})")
        lines += ["", "</details>", ""]
    lines += [
        "## 你的意见如何进入下一轮",
        "",
        "你直接按编号回复即可，不必编辑JSON或打开源码。我们保留你的原话，再分别标注错误环节、"
        "期望动作、适用条件与证据范围；存在歧义时只追问对应条目，不替你补出“专家同意”。",
        "",
        "未回复的卡片保持“未评审”，不会变成负例或正例。专家意见先作为待整理反馈，"
        "不能直接当作训练奖励、已验证Memory或有效Skill。结合最终评测提出的意见会单独标为事后信息。",
        "",
        "查询小卡只整理已核实的关键查询，不代表每条历史查询都有完整解释。没有当时说明的知识取舍标“未说明”；"
        "返回的源码或数据不自动等于Agent已经理解、采纳或正确实现。",
        "",
        "[完整动作时间线](DECISION_TIMELINE.md) · [Notebook历史](NOTEBOOK.md) · "
        "[技术附录](TECHNICAL_AUDIT.md)",
        "",
    ]
    return "\n".join(lines)


def feedback_template(bundle):
    return {
        "schema": "expert-feedback.v1",
        "packet_sha256": sha(bundle),
        "contract_hash": bundle["contract_hash"],
        "training_eligible": False,
        "items": [
            {
                "card_id": c["id"],
                "card_sha256": sha(c),
                "review_status": "unreviewed",
                "verdict": None,
                "error_layers": [],
                "expert_raw_text": None,
                "preferred_action": None,
                "applicability": None,
                "basis": None,
            }
            for c in bundle["cards"]
        ],
    }


def record_feedback(
    directory,
    bundle,
    *,
    feedback_id,
    card_id,
    verdict,
    error_layers,
    expert_raw_text,
    preferred_action,
    applicability,
    basis,
    source_message,
):
    """Only call after the user actually supplies this feedback; never infer it.

    Revisions append a new feedback ID bound to the exact old card. This is
    a curation record, not an executable training example or protocol approval.
    """
    from kinetic_agents.observability.review import plain
    from kinetic_agents.observability.review import scrub

    if (
        not isinstance(feedback_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", feedback_id)
        or (verdict is not None and verdict not in VERDICTS)
    ):
        raise ValueError("explicit feedback identity and verdict required")
    matches = [c for c in bundle["cards"] if c["id"] == card_id]
    if (
        len(matches) != 1
        or not isinstance(error_layers, list)
        or any(not isinstance(layer, str) or layer not in LAYERS for layer in error_layers)
    ):
        raise ValueError("one known card and supported error layers required")
    if basis not in ("at_decision", "retrospective_with_results"):
        raise ValueError("decision-time versus hindsight feedback must be distinguished")
    for field in (expert_raw_text, source_message):
        if not isinstance(field, str) or not field.strip() or len(field) > 8000:
            raise ValueError("actual expert statement and attribution required")
    for field in (preferred_action, applicability):
        if field is not None and (
            not isinstance(field, str) or not field.strip() or len(field) > 8000
        ):
            raise ValueError("unspecified expert advice stays null, never invented")
    value = dict(
        schema="expert-feedback.v1",
        feedback_id=feedback_id,
        card_id=card_id,
        contract_hash=bundle["contract_hash"],
        packet_sha256=sha(bundle),
        card_sha256=sha(matches[0]),
        verdict=verdict,
        error_layers=error_layers,
        expert_raw_text=expert_raw_text,
        preferred_action=preferred_action,
        applicability=applicability,
        basis=basis,
        source_message=source_message,
        training_eligible=False,
        review_status="recorded_not_validated",
    )
    value = scrub(value)
    directory = plain(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with locked(plain(directory / "feedback.lock")):
        path = plain(directory / (feedback_id + ".json"))
        if path.exists():
            old = json.loads(path.read_text())
            if {k: v for k, v in old.items() if k != "recorded_at"} != value:
                raise PermissionError("feedback is append-only; use a new ID for a correction")
            return old
        value["recorded_at"] = time.time()
        atomic(path, value)
    return value
