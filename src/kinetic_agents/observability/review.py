"""Versioned experiment postmortems, built from evidence, not hidden reasoning.

Host-only exporter: reads only an explicit run; never calls a model/solver/SSH.
Original ledgers remain authoritative. Search-only machine view is built BEFORE
any endpoint is opened. Native actions, explicit statements and inferred failure
signals have different types. Missing explanations are not invented.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time

from kinetic_agents.core.storage import atomic
from kinetic_agents.core.storage import locked
from kinetic_agents.observability.notebook_tools import SCHEMA as DECISION_SCHEMA
from kinetic_agents.observability.notebook_tools import QUERY_SCHEMA

SCHEMA = "experiment-review.v1"
MAX_ROWS = 100000
MAX_BYTES = 256 * 1024 * 1024
MAX_LINE = 4 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def plain(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise PermissionError("plain owned review paths required")
    if path.exists() and not path.is_dir():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("regular unlinked source required")
    return path


def scrub(value):
    """Defense in depth for local reports; NOT authorization to publish logs."""
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(r"password|api.?key|authorization|credential|secret", k, re.I)
                else scrub(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
        value = re.sub(r"(?i)(Bearer\s+)\S+", r"\1[REDACTED]", value)
        return re.sub(
            r"(?i)((?:api[_-]?key|password|secret|authorization)\s*[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
    return value


class Sources:
    def __init__(self, root):
        self.root = plain(root)
        self.sources = []
        self.gaps = []

    def read(self, name):
        path = plain(self.root / name)
        if not path.exists():
            self.gaps.append({"source": name, "reason": "missing"})
            return None
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("source exceeds bounded review input")
        raw = path.read_bytes()
        self.sources.append(
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        )
        return json.loads(raw)

    def text(self, name):
        path = plain(self.root / name)
        if not path.exists():
            self.gaps.append({"source": name, "reason": "missing"})
            return None
        if path.stat().st_size > MAX_LINE:
            raise ValueError("task text exceeds bounded review input")
        raw = path.read_bytes()
        self.sources.append(
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        )
        return raw.decode()

    @contextmanager
    def database(self, name):
        path = plain(self.root / name)
        if not path.exists():
            self.gaps.append({"source": name, "reason": "missing"})
            yield None
            return
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    def rows(self, db, source, query):
        rows = db.execute(query + " LIMIT ?", (MAX_ROWS + 1,)).fetchall()
        if len(rows) > MAX_ROWS:
            self.gaps.append({"source": source, "reason": "row_limit", "limit": MAX_ROWS})
        rows = rows[:MAX_ROWS]
        self.sources.append({"path": source, "projection_sha256": digest(rows), "rows": len(rows)})
        return rows


def select(value, keys):
    return {k: value[k] for k in keys if k in value}


def utc(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value or "unknown")


def runtime_snapshot(s):
    result = {"state": {}, "events": [], "jobs": [], "cost": {}}
    with s.database("runtime.sqlite") as db:
        if db is None:
            return result
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = s.rows(
            db,
            "runtime.sqlite#state",
            "SELECT key,value FROM state WHERE key IN ('status','contract','contract_hash','started','deadline','science_started','qualification_cpu_charged_seconds','prior_attempt_cpu_seconds') ORDER BY key",
        )
        state = {k: json.loads(v) for k, v in rows}
        result["state"] = state
        events = s.rows(
            db,
            "runtime.sqlite#events",
            "SELECT seq,at,kind,payload FROM events WHERE kind NOT IN ('RR_HEARTBEAT','API_RESERVED','API_COMPLETED','COORDINATOR_LEASE') ORDER BY seq",
        )
        keys = (
            "action_id",
            "thread_id",
            "turn_id",
            "tool_name",
            "arguments_sha256",
            "result_sha256",
            "error_type",
            "duration_seconds",
            "queue_wait_seconds",
            "status",
            "id",
            "job_id",
        )
        for seq, at, kind, payload in events:
            result["events"].append(
                {
                    "id": "runtime:" + str(seq),
                    "at": utc(at),
                    "kind": kind,
                    "source": "runtime.sqlite#events/" + str(seq),
                    "data": select(json.loads(payload), keys),
                    "epistemic_status": "recorded_event",
                }
            )
        if "remote_jobs" in tables:
            for ident, raw in s.rows(
                db,
                "runtime.sqlite#remote_jobs",
                "SELECT id,value FROM remote_jobs ORDER BY id",
            ):
                row = json.loads(raw)
                item = select(
                    row,
                    (
                        "id",
                        "actor",
                        "status",
                        "job_id",
                        "created",
                        "charged_seconds",
                        "reserved_seconds",
                        "output_status",
                        "error_type",
                        "execution_receipt",
                    ),
                )
                item["source"] = "runtime.sqlite#remote_jobs/" + ident
                # Only the trusted exit code, not arbitrary output or arguments.
                if isinstance(item.get("execution_receipt"), dict):
                    item["execution_receipt"] = select(item["execution_receipt"], ("exit_code",))
                result["jobs"].append(item)
        if "calls" in tables:
            amount, reserve, count, unknown = db.execute(
                "SELECT COALESCE(SUM(charge),0), COALESCE(SUM(CASE WHEN charge IS NULL THEN reserve ELSE 0 END),0), COUNT(*), SUM(CASE WHEN charge IS NULL THEN 1 ELSE 0 END) FROM calls"
            ).fetchone()
            result["cost"] = {
                "budget_settled_usd": amount,
                "unknown_reserved_usd": reserve,
                "request_count": count,
                "unknown_usage_requests": unknown or 0,
                "interpretation": "conservative allowance accounting, NOT provider invoice",
            }
    return result


def team_snapshot(s):
    result = {"records": [], "events": [], "decisions": [], "queries": []}
    with s.database("team/team.sqlite") as db:
        if db is None:
            return result
        rows = s.rows(
            db,
            "team/team.sqlite#records",
            "SELECT kind,id,version,value FROM records ORDER BY kind,id,version",
        )
        for kind, ident, version, raw in rows:
            value = json.loads(raw)
            ref = {
                "kind": kind,
                "id": ident,
                "version": version,
                "source": f"team/team.sqlite#records/{kind}/{ident}/{version}",
            }
            # Explicit notebook text is not a hidden model reasoning field.
            result["records"].append(
                {
                    **ref,
                    "record": scrub(value),
                    "epistemic_status": "recorded_notebook_not_science_verification",
                }
            )
            if kind == "document":
                try:
                    decision = json.loads(value.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(decision, dict) and decision.get("schema") == DECISION_SCHEMA:
                    result["decisions"].append(
                        {
                            **ref,
                            "actor": value.get("editor"),
                            "references": value.get("references", []),
                            **scrub(decision),
                        }
                    )
                elif isinstance(decision, dict) and decision.get("schema") == QUERY_SCHEMA:
                    result["queries"].append(
                        {
                            **ref,
                            "actor": value.get("editor"),
                            "references": value.get("references", []),
                            **scrub(decision),
                        }
                    )
        for seq, at, actor, operation, ident, version in s.rows(
            db,
            "team/team.sqlite#events",
            "SELECT seq,at,actor,operation,object_id,version FROM events ORDER BY seq",
        ):
            result["events"].append(
                {
                    "id": "team:" + str(seq),
                    "at": utc(at),
                    "actor": actor,
                    "operation": operation,
                    "object_id": ident,
                    "version": version,
                    "source": "team/team.sqlite#events/" + str(seq),
                    "epistemic_status": "recorded_event",
                }
            )
    return result


def native_snapshot(s):
    """Stream only public messages and tool actions. Ignore analysis/reasoning."""
    from kinetic_agents.observability.steps import request_observation
    from kinetic_agents.observability.steps import output_observation

    base = plain(s.root / "native/codex/sessions")
    result = {
        "actions": [],
        "statements": [],
        "sessions": [],
        "ignored_reasoning_records": 0,
        "unhandled_call_types": {},
    }
    if not base.exists():
        s.gaps.append({"source": "native/codex/sessions", "reason": "missing"})
        return result
    used = 0
    for directory, children, files in os.walk(base, followlinks=False):
        children[:] = sorted(n for n in children if not Path(directory, n).is_symlink())
        for name in sorted(files):
            if not name.endswith(".jsonl"):
                continue
            path = plain(Path(directory, name))
            relative = path.relative_to(s.root).as_posix()
            if used + path.stat().st_size > MAX_BYTES:
                s.gaps.append({"source": relative, "reason": "native_byte_limit"})
                continue
            hasher = hashlib.sha256()
            actor = path.stem
            calls = {}
            lines = 0
            actor_bound = False
            with path.open("rb") as stream:
                while True:
                    raw = stream.readline(MAX_LINE + 1)
                    if not raw:
                        break
                    if len(raw) > MAX_LINE:
                        raise ValueError("native record exceeds review line limit")
                    used += len(raw)
                    hasher.update(raw)
                    lines += 1
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        s.gaps.append(
                            {
                                "source": relative + ":" + str(lines),
                                "reason": "invalid_json_line",
                            }
                        )
                        continue
                    p = event.get("payload") or {}
                    typ = p.get("type")
                    if event.get("type") == "session_meta" and not actor_bound:
                        actor = p.get("id", actor)
                        actor_bound = True
                        result["sessions"].append(
                            select(
                                p,
                                (
                                    "id",
                                    "parent_thread_id",
                                    "forked_from_id",
                                    "agent_nickname",
                                    "subagent_history_start_ordinal",
                                ),
                            )
                        )
                    if (
                        typ == "reasoning"
                        or p.get("channel") == "analysis"
                        or p.get("phase") == "analysis"
                        or typ == "agent_reasoning"
                    ):
                        result["ignored_reasoning_records"] += 1
                        continue
                    ref = {
                        "source": relative,
                        "line": lines,
                        "at": utc(event.get("timestamp")),
                        "actor": actor,
                    }
                    if event.get("type") == "response_item" and typ in (
                        "function_call",
                        "custom_tool_call",
                    ):
                        call_id = p.get("call_id", str(lines))
                        arguments = p.get("arguments", p.get("input", ""))
                        try:
                            args = (
                                json.loads(arguments) if isinstance(arguments, str) else arguments
                            )
                        except ValueError:
                            args = {}
                        command = (
                            args.get("cmd", args.get("command", ""))
                            if isinstance(args, dict)
                            else ""
                        )
                        row = {
                            **ref,
                            "id": f"{actor}:{call_id}",
                            "call_id": call_id,
                            "tool": p.get("name"),
                            "arguments_sha256": digest(arguments),
                            "command_excerpt": scrub(str(command)[:1200]),
                            "request_excerpt": encoded(scrub(args))[:2000],
                            "request_excerpt_may_be_truncated": len(encoded(scrub(args))) > 2000,
                            "request_observation": request_observation(p.get("name"), scrub(args)),
                            "outputs": [],
                            "argument_keys": (sorted(args) if isinstance(args, dict) else []),
                            "outcome": "not_observed",
                            "rationale": None,
                            "epistemic_status": "observed_action_not_explanation",
                        }
                        result["actions"].append(row)
                        calls[call_id] = row
                    elif event.get("type") == "response_item" and typ in (
                        "function_call_output",
                        "custom_tool_call_output",
                    ):
                        row = calls.get(p.get("call_id"))
                        if row is not None:
                            # Keep original output address/hash, not a misleading successful-tool flag.
                            output = p.get("output", "")
                            row.update(
                                outcome="output_recorded_not_scientific_success",
                                output_source=ref,
                                output_sha256=digest(output),
                            )
                            try:
                                clean_output = (
                                    scrub(json.loads(output))
                                    if isinstance(output, str)
                                    else scrub(output)
                                )
                            except ValueError:
                                clean_output = scrub(output)
                            row["outputs"].append(
                                {
                                    **ref,
                                    "sha256": digest(output),
                                    "observation": output_observation(clean_output),
                                }
                            )
                    elif (
                        event.get("type") == "response_item"
                        and typ == "message"
                        and p.get("role") == "assistant"
                        and (p.get("channel") or p.get("phase"))
                        in ("commentary", "final", "final_answer")
                    ):
                        message = "\n".join(
                            c.get("text", "") for c in p.get("content", []) if isinstance(c, dict)
                        )
                        result["statements"].append(
                            {
                                **ref,
                                "text": scrub(message),
                                "channel": p.get("channel") or p["phase"],
                                "epistemic_status": "agent_statement_not_verified",
                            }
                        )
                    elif (
                        event.get("type") == "response_item"
                        and isinstance(typ, str)
                        and typ.endswith("_call")
                    ):
                        result["unhandled_call_types"][typ] = (
                            result["unhandled_call_types"].get(typ, 0) + 1
                        )
                    if len(result["actions"]) + len(result["statements"]) > MAX_ROWS:
                        raise ValueError("native action limit exceeded; use a bounded shard")
            s.sources.append(
                {
                    "path": relative,
                    "sha256": hasher.hexdigest(),
                    "bytes": path.stat().st_size,
                    "lines": lines,
                }
            )
    return result


def search_report(root, *, include_native=True):
    s = Sources(root)
    runtime = runtime_snapshot(s)
    team = team_snapshot(s)
    binding = runtime["state"].get("contract", {}).get("backend", {})
    if binding.get("auth") == "api":
        runtime["cost"] = {
            "billing": "configured API",
            "api_usd": None,
            "token_usage": s.read("native/subscription_usage.json"),
            "request_audit": "native/api_requests.jsonl",
            "interpretation": "No dollar cap configured; token records are not a provider invoice",
        }
    elif (
        runtime["state"].get("contract", {}).get("billing")
        == "chatgpt_subscription_not_metered_API"
    ):
        runtime["cost"] = {
            "billing": "ChatGPT subscription",
            "api_usd": None,
            "token_usage": s.read("native/subscription_usage.json"),
            "account_quota": s.read("native/subscription_quota.json"),
            "interpretation": "Subscription usage, NOT zero-cost inference or a per-run dollar invoice",
        }
    if include_native and runtime["state"].get("contract", {}).get("harness", {}).get("name") in (
        "kimi_code",
        "claude_code",
    ):
        from kinetic_agents.observability.stream_review import snapshot

        native = snapshot(s)
    else:
        native = native_snapshot(s) if include_native else {"actions": [], "statements": []}
    ended = s.read("result.json")
    cpu = s.read("local_cpu.json")
    from kinetic_agents.core.inputs import task_directory

    task_path = task_directory(root) / "TASK.md"
    task = task_path.read_text() if task_path.is_file() else None
    failures = []
    for row in runtime["jobs"]:
        exit_code = (row.get("execution_receipt") or {}).get("exit_code")
        if row["status"] in ("REJECTED", "SUBMIT_UNCERTAIN") or (
            exit_code is not None and exit_code != 0
        ):
            failures.append(
                {
                    "layer": "execution",
                    "fact": {
                        "job_id": row["id"],
                        "status": row["status"],
                        "exit_code": exit_code,
                    },
                    "source": row["source"],
                    "cause_status": "unresolved",
                    "next_check": "检查程序日志与调度回执，区分科学代码、求解器和基础设施故障。",
                }
            )
    return scrub(
        {
            "schema": SCHEMA,
            "run_id": s.root.name,
            "run_root": str(s.root),
            "audience": "search_agent",
            "scope": "this_run_search_only",
            "benchmark_feedback_included": False,
            "state": runtime["state"],
            "task_text": task,
            "terminal_result": select(
                ended or {}, ("status", "search_deadline", "scientifically_verified")
            ),
            "submission_present": bool((ended or {}).get("submission")),
            "submission_outcome": ((ended or {}).get("submission") or {}).get("outcome"),
            "model_account": runtime["cost"],
            "search_local_cpu": cpu,
            "jobs": runtime["jobs"],
            "runtime_events": runtime["events"],
            "team_events": team["events"],
            "notebook": team["records"],
            "decisions": team["decisions"],
            "queries": team["queries"],
            "native": native,
            "failure_signals": failures,
            "coverage": {
                "explicit_decision_versions": len(team["decisions"]),
                "explicit_query_versions": len(team["queries"]),
                "query_interpretation_completeness": "not_guaranteed",
                "native_actions": len(native["actions"]),
                "explanation_completeness": "not_guaranteed",
                "missing_reasons_are_unknown": True,
                "sources_missing_or_limited": s.gaps,
                "unhandled_native_call_types": native.get("unhandled_call_types", {}),
            },
            "sources": s.sources,
            "boundaries": [
                "No hidden reasoning exported.",
                "A retrieved memory is not proof of use; successful execution is not proof of scientific benefit.",
                "An after-the-fact expert interpretation is not the Agent's recorded rationale.",
                "Future-run access requires the next experiment contract; no automatic cross-run memory promotion.",
            ],
        }
    )


def endpoint_report(s):
    prefix = "endpoint/"
    card_name, result_name, hash_key = "scorecard.json", "evaluation_result.json", "result_sha256"
    if (s.root / "endpoint_bulk_v1/repair_request.json").is_file():
        prefix = "endpoint_bulk_v1/"
        card_name, result_name, hash_key = "combined_scorecard.json", "combined_result.json", "combined_result_sha256"
    card = s.read(prefix + card_name)
    if card is None:
        return {"status": "NOT_AVAILABLE", "feedback_allowed": False}
    result = s.read(prefix + result_name)
    if (
        result is None
        or digest_bytes(s.root / (prefix + result_name)) != card[hash_key]
    ):
        raise ValueError("endpoint result/card identity mismatch")
    if card["scores"] != result["scores"] or card["leaderboard"] != result["leaderboard"]:
        raise ValueError("scorecard projection disagrees with the locked result")
    # Use the already verified scorer's fields; do not invent a new ranking or weights.
    failures = [
        select(r, ("label", "candidate_id", "case_id", "status", "missing_species"))
        for r in result["rows"]
        if r["status"] != "success"
    ]
    return {
        "status": card["status"],
        "feedback_allowed": False,
        "scores": card["scores"],
        "leaderboard": card["leaderboard"],
        "failures": failures,
        "allocated_core_seconds": card.get("evaluation_allocated_core_seconds"),
        "result_sha256": card[hash_key],
        "source": prefix + card_name,
        "interpretation": "既有历史暴露终点评价；不是新盲测或RSI因果收益证据。",
    }


def digest_bytes(path):
    return hashlib.sha256(plain(path).read_bytes()).hexdigest()


def markdown(report):
    s = report["search"]
    endpoint = report.get("endpoint", {})

    def cell(value):
        return str(value if value is not None else "未记录").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# 实验滚动复盘 — " + s["run_id"],
        "",
        "这是按原始记录生成的诊断报告，不是模型内部思维链，也不是自动确认的因果解释。",
        "",
        "## 1. 任务、终态与记录完整性",
        "",
        "- 契约身份：`" + str(s["state"].get("contract_hash", "未记录")) + "`",
        "- 原运行终态：" + str(s["state"].get("status", "未记录")),
        "- 已锁定提交："
        + ("是" if s["submission_present"] else "未发现")
        + "；提交结果："
        + str(s["submission_outcome"])
        + "；独立评测状态："
        + str(endpoint.get("status", "未读取")),
        "- 状态标签不等于停止原因；若与提交/评测矛盾，按事件时序核对，不能直接归因预算耗尽。",
        "- 科研目标原文：本运行 `task/TASK.md`；本报告不改写任务。",
        f"- 原生工具动作 {len(s['native']['actions'])}；显式决策版本 {len(s['decisions'])}。两者不是同一个计数。",
        "- 理由覆盖：不保证完整；没有当时的记录就标未知，不能事后补成模型动机。",
        "- 缺失/限长来源：" + encoded(s["coverage"]["sources_missing_or_limited"]),
        "",
        "- 尚未解析的原生动作类型：" + encoded(s["coverage"]["unhandled_native_call_types"]),
        "",
        "## 2. 决策与实际执行",
        "",
        "当时的决定/简短依据来自版本化决策记录；工具完整索引、公开进度说明见同版本 report.json。",
        "",
    ]
    if not s["decisions"]:
        lines += [
            "旧轨迹没有使用结构化决策接口；以下不能被自动补造成完整决策理由。",
            "",
        ]
    for d in s["decisions"]:
        lines += [
            f"### {d['id']} v{d['version']} · {d['stage']}",
            "",
            "- 决定：" + d["decision"],
            "- 当时声明的依据（未经核验）：" + d["reason_summary"],
            "- 考虑的替代方案：" + encoded(d["alternatives"]),
            "- 已记录结果：" + d["outcome"],
            "- 重新考虑条件：" + d["revisit_when"],
            "- 证据引用：`" + encoded(d["references"]) + "`",
            "- 来源：`" + d["source"] + "`",
            "",
        ]
    lines += [
        "### 公开进度说明预览（不是已验证结论）",
        "",
        "[完整动作与公开说明时间线](DECISION_TIMELINE.md)；[全部Notebook历史版本](NOTEBOOK.md)。",
        "",
    ]
    statements = sorted(s["native"]["statements"], key=lambda r: (r["at"], r["source"], r["line"]))
    lines.append(f"以下仅预览最早8条，共{len(statements)}条；完整时间线不按重要性删选。")
    for message in statements[:8]:
        lines += [
            f"- {message['at']} · {message['actor']} · `{message['source']}:{message['line']}`",
            "  " + cell(message["text"][:2000]),
        ]
    if not statements:
        lines.append("无可用公开说明。")
    lines += [
        "",
        "## 3. 候选及独立终点评价",
        "",
        "搜索中的自选验证与终点评价不能混用。以下只抄录既有权威评分；未启用60/40草案。",
        "",
    ]
    if endpoint.get("leaderboard"):
        lines += ["|候选|物种|反应|覆盖率|完整池平均误差|", "|---|---:|---:|---:|---:|"]
        for row in endpoint["leaderboard"]["rows"]:
            values = [
                row.get(k) for k in ("label", "species", "reactions", "coverage", "mean_abs_sigma")
            ]
            values[-1] = round(values[-1], 6) if values[-1] is not None else "覆盖不足，不成立"
            values[-2] = f"{values[-2]:.2%}" if isinstance(values[-2], (int, float)) else "未记录"
            lines.append("|" + "|".join(cell(v) for v in values) + "|")
        lines += [
            "",
            "逐工况失败原因及燃料×观测组统计见 report.json 的 endpoint；不进入 Agent 搜索视图。",
        ]
    else:
        lines += ["终点评价尚不可用，不能以搜索自报结果代替。"]
    lines += [
        "",
        "## 4. 失败模式：事实、可能原因与待验证项",
        "",
        "|检查环节|本报告可以核实什么|不能自动下的结论|",
        "|---|---|---|",
        "|任务理解与工况选择|任务版本、澄清、显式选点理由与原始脚本入口|工况少一定是错误、或一定由预算导致|",
        "|方法与物化工具|工具调用、代码/结果地址、声明的接受条件|工具返回就说明算法正确|",
        "|执行与基础设施|作业终态、退出码、成本|非零退出一定是模型/求解器能力问题|",
        "|候选与验证|固定评测覆盖率、原始误差和失败工况|成功子集均值等于完整池平均误差|",
        "|记忆与协作|Memory/Skill版本、上下文读取和执行回执|检索到就实际依赖、修订就产生收益|",
        "|停止决策|提交/预算/故障时序、显式停止理由|停止代表已经找到最优解|",
        "",
    ]
    for failure in s["failure_signals"]:
        lines += [
            "- 已记录执行信号：`"
            + encoded(failure["fact"])
            + "`；来源 `"
            + failure["source"]
            + "`。原因未定；"
            + failure["next_check"]
        ]
    failures = endpoint.get("failures", [])
    if failures:
        lines += [
            "- 终点评价失败类型：`"
            + encoded(dict(Counter(r["status"] for r in failures)))
            + "`。机理×工况记录数，不是独立case数；详细ID见JSON。"
        ]
    for finding in report.get("expert_findings", []):
        lines += [
            "",
            "### 人类/复盘者分析：" + finding["title"],
            "",
            "- 证据等级：" + finding["epistemic_status"],
            "- 判断：" + finding["analysis"],
            "- 证据：" + encoded(finding["sources"]),
            "- 下一验证：" + finding["next_check"],
        ]
    lines += [
        "",
        "## 5. 时间与成本",
        "",
        "- 模型保守账本（非实际扣款）：`" + encoded(s["model_account"]) + "`",
        "- 本地搜索CPU：`" + encoded(s["search_local_cpu"]) + "`",
        "- 远端各作业的分配核秒及退出码：report.json → search.jobs。",
        "- 独立终点评测分配核秒：" + str(endpoint.get("allocated_core_seconds", "未记录")),
        "- 时间重叠、等待、失败和未结算值不能简单相加当模型思考时间；不重置历史费用。",
        "",
        "## 6. Memory / Skill 与实际使用",
        "",
        "记录数（包括历史版本，不代表独立技能数或进化次数）：`"
        + encoded(dict(Counter(r["kind"] for r in s["notebook"])))
        + "`",
        "每个对象的版本、支持证据、反例、检索与Use记录保留在 report.json → search.notebook。",
        "",
        "## 7. 下一轮问题与访问边界",
        "",
        "- 先检查尚无依据的决策与未定原因，再决定修任务、方法、验证、基础设施还是停止策略。",
        "- 自动检测仅产生失败信号；具体因果归因需要对照实算或代码核验。",
        "- 搜索Agent读取 SEARCH_REVIEW.json / research_review_read；不自动读取本完整报告。",
        "- 终点评价和专家后验分析不能偷偷回流原搜索；跨轮复用要按下一轮契约授权。",
        "- 每次导出保留内容哈希版本；后续结论修订不覆盖旧报告。",
        "",
    ]
    return "\n".join(lines)


def timeline_markdown(search):
    lines = [
        "# 完整可观察动作与公开说明时间线",
        "",
        "动作与说明按时间合并。先后出现不证明因果关联；没有当时理由的动作仍标为未知。",
        "命令只展示前240字符，完整原始参数/输出按来源行号回查；不导出隐藏推理。",
        "",
    ]
    items = [(r, "action") for r in search["native"]["actions"]]
    items += [(r, "statement") for r in search["native"]["statements"]]
    for row, kind in sorted(
        items, key=lambda item: (item[0]["at"], item[0]["source"], item[0]["line"])
    ):
        source = str(Path(search["run_root"]) / row["source"]) + ":" + str(row["line"])
        lines += [f"## {row['at']} · {row['actor']}", "", f"[原始记录]({source})", ""]
        if kind == "statement":
            lines += ["类型：Agent公开说明，未经独立核验。", "", row["text"], ""]
        else:
            command = row["command_excerpt"][:240].replace("`", "'")
            lines += [
                "- 工具：`" + str(row["tool"]) + "`；调用ID：`" + row["call_id"] + "`",
                "- 命令预览：`" + command + "`",
                "- 返回状态：" + row["outcome"],
                "- 当时理由：未逐条关联；不能从工具名反推。",
                "",
            ]
    return "\n".join(lines)


def notebook_markdown(search):
    lines = [
        "# 实验Notebook：全部已保存版本",
        "",
        "这些是当时保存的任务、记忆、技能、证据和使用记录，不自动等于正确科学结论。",
        "",
    ]
    for row in search["notebook"]:
        lines += [
            f"## {row['kind']} · {row['id']} · v{row['version']}",
            "",
            "来源：`" + row["source"] + "`",
            "",
            "```json",
            json.dumps(row["record"], ensure_ascii=False, sort_keys=True, indent=2).replace(
                "```", "\\u0060\\u0060\\u0060"
            ),
            "```",
            "",
        ]
    return "\n".join(lines)


def publish(
    root,
    *,
    endpoint=False,
    output=None,
    include_native=True,
    expert_findings=None,
    expert_cards=None,
):
    import kinetic_agents.observability.expert as expert_review
    import kinetic_agents.observability.steps as step_review

    root = plain(root)
    search = search_report(root, include_native=include_native)
    report = {
        "schema": SCHEMA,
        "exporter_source_sha256": digest_bytes(Path(__file__)),
        "search": search,
        "endpoint_included": endpoint,
        "expert_renderer_source_sha256": digest_bytes(Path(expert_review.__file__)),
        "step_renderer_source_sha256": digest_bytes(Path(step_review.__file__)),
        "expert_review_ready": expert_cards is not None,
        "expert_cards": expert_cards,
        "expert_findings": expert_findings or [],
        "interpretation": "evidence-backed postmortem, not causal proof",
    }
    if endpoint:
        sources = Sources(root)
        report["endpoint"] = endpoint_report(sources)
        report["endpoint_sources"] = sources.sources
    for f in report["expert_findings"]:
        if (
            set(f) != {"title", "analysis", "epistemic_status", "sources", "next_check"}
            or f["epistemic_status"] not in ("verified_fact", "inference", "hypothesis")
            or not f["sources"]
        ):
            raise ValueError("expert analysis requires provenance and explicit epistemic status")
    report = scrub(report)
    if report["expert_cards"] is not None:
        expert_review.validate(report["expert_cards"], report)
    steps = step_review.build(search, report["expert_cards"])
    report["step_review_counts"] = steps["counts"]
    revision = digest(report)
    destination = plain(output or root / "review")
    if any(destination.is_relative_to(root / name) for name in ("work", "task", "native")):
        raise PermissionError(
            "host review cannot be placed in scientist input/work/session directories"
        )
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(plain(destination / "publish.lock")):
        folder = plain(destination / "versions" / revision)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        files = {
            "report.json": encoded(report) + "\n",
            "SEARCH_REVIEW.json": encoded(search) + "\n",
            "EXPERIMENT_REPORT.md": expert_review.render(report["expert_cards"], report),
            "TECHNICAL_AUDIT.md": markdown(report),
            "DECISION_TIMELINE.md": timeline_markdown(search),
            "NOTEBOOK.md": notebook_markdown(search),
        }
        files.update(step_review.documents(steps))
        files["STEP_REVIEW.json"] = encoded(steps) + "\n"
        if report["expert_cards"] is not None:
            files["EXPERT_CARDS.json"] = encoded(report["expert_cards"]) + "\n"
            files["FEEDBACK_TEMPLATE.json"] = (
                encoded(expert_review.feedback_template(report["expert_cards"])) + "\n"
            )
        for name, data in files.items():
            target = plain(folder / name)
            if target.exists():
                if target.read_text() != data:
                    raise PermissionError("immutable report revision changed")
            else:
                with target.open("x") as stream:
                    stream.write(data)
        pointer = {
            "schema": SCHEMA,
            "revision": revision,
            "directory": "versions/" + revision,
            "search_view_sha256": digest(search),
            "report_sha256": digest(report),
            "expert_review_ready": report["expert_review_ready"],
            "endpoint_included": endpoint,
            "files": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()},
        }
        atomic(plain(destination / "LATEST.json"), pointer)
        target = plain(destination / "EXPERIMENT_REPORT.md")
        # The landing page is a projection, never the canonical report.
        temporary = plain(destination / "EXPERIMENT_REPORT.md.tmp")
        landing = files["EXPERIMENT_REPORT.md"]
        for name in files:
            if name.endswith(".md"):
                landing = landing.replace(f"]({name})", f"](versions/{revision}/{name})")
        temporary.write_text(landing)
        temporary.replace(target)
        step_landing = files["STEP_BY_STEP.md"]
        for name in files:
            if name.endswith(".md"):
                step_landing = step_landing.replace(f"]({name})", f"](versions/{revision}/{name})")
        temporary = plain(destination / "STEP_BY_STEP.md.tmp")
        temporary.write_text(step_landing)
        temporary.replace(plain(destination / "STEP_BY_STEP.md"))
    return {"status": "PUBLISHED", **pointer, "output": str(destination)}


def attempt(root, *, endpoint):
    """Bounded child inside the existing post-run CPU owner; never hides failure."""
    command = [
        sys.executable,
        "-B",
        "-m",
        "kinetic_agents.observability.review",
        "build",
        "--root",
        str(root),
    ]
    if endpoint:
        command.append("--endpoint")
    try:
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60
        )
        status = {
            "status": "PUBLISHED" if result.returncode == 0 else "FAILED",
            "exit_code": result.returncode,
        }
        status["expert_review_ready"] = False  # Collection is not Chinese interpretation.
        if result.returncode:
            status["error_excerpt"] = scrub(result.stderr.decode(errors="replace")[-3000:])
    except subprocess.TimeoutExpired:
        status = {"status": "PENDING_RETRY", "reason": "report_wall_timeout"}
    status = {
        **status,
        "at": time.time(),
        "endpoint_requested": endpoint,
        "model_calls": 0,
        "scientific_solves": 0,
        "original_result_unchanged": True,
    }
    atomic(
        plain(Path(root) / "review_build_attempts" / (str(time.time_ns()) + ".json")),
        status,
    )
    atomic(plain(Path(root) / "review_build_status.json"), status)
    return status


def finish(root, release, *, evaluate):
    """Post-run owner path, not a chat monitor or a second research Agent."""
    attempt(root, endpoint=False)
    try:
        if evaluate:
            from kinetic_agents.evaluation.submission import execute

            execute(root, release)
    finally:
        attempt(root, endpoint=evaluate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "finish"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--endpoint", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--expert-findings", type=Path)
    parser.add_argument("--expert-cards", type=Path)
    args = parser.parse_args()
    if args.operation == "build":
        if args.evaluate:
            parser.error("build never executes evaluation")
        findings = (
            json.loads(plain(args.expert_findings).read_text()) if args.expert_findings else None
        )
        cards = json.loads(plain(args.expert_cards).read_text()) if args.expert_cards else None
        start_cpu = time.process_time()
        start_wall = time.monotonic()
        value = publish(
            args.root,
            endpoint=args.endpoint,
            output=args.output,
            expert_findings=findings,
            expert_cards=cards,
        )
        receipt = {
            "postprocessing_cpu_seconds": time.process_time() - start_cpu,
            "wall_seconds": time.monotonic() - start_wall,
            "model_calls": 0,
            "scientific_solves": 0,
            "accounting": "local report overhead; included in enclosing owner when run by coordinator",
            "at": time.time(),
            "revision": value["revision"],
        }
        atomic(plain(Path(value["output"]) / "BUILD_RECEIPT.json"), receipt)
        print(encoded({**value, "build_receipt": receipt}))
    else:
        if (
            args.output
            or args.endpoint
            or args.expert_findings
            or args.expert_cards
            or (args.evaluate and not args.release)
        ):
            parser.error("finish requires the owned lifecycle arguments")
        finish(args.root, args.release, evaluate=args.evaluate)
