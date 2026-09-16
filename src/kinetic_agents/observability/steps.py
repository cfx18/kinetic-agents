"""Lossless enumeration of observable steps; conservative Chinese UI labels.

No command execution, no inferred motives, no evaluator reads. Labels describe
tool syntax, not scientific intent. Every action/public statement gets a row;
source references remain authoritative when excerpts or parsing are limited.
"""

from hashlib import sha256
import html
import json
from pathlib import Path
import re
import shlex
from urllib.parse import parse_qs
from urllib.parse import urlsplit

PAGE_SIZE = 25
LABELS = {
    "exec_command": "执行本地命令",
    "write_stdin": "读取或控制已有本地进程",
    "research_budget": "查看剩余资源",
    "research_compute_status": "查询远端作业状态",
    "research_compute_submit": "提交远端计算",
    "research_compute_read": "读取远端结果片段",
    "research_compute_fetch": "取回远端结果文件",
    "research_record_artifact": "登记证据文件",
    "research_record_decision": "记录决策依据",
    "research_record_query": "记录查询及知识取舍",
    "research_review_read": "回查已有科研记录",
    "research_skill_compute_submit": "提交技能计算",
    "finish_research": "提交最终交付物",
    "team_list": "查看团队记录目录",
    "team_read": "读取团队记录",
    "team_messages": "接收团队消息",
    "team_ack": "确认收到消息",
    "team_claim": "认领任务",
    "team_delegate": "分配任务",
    "team_review": "审查任务交付",
    "team_submit": "提交子任务结果",
    "team_note": "保存科研笔记",
    "team_ask": "向主Agent提问",
    "team_answer": "回答研究员问题",
    "team_cancel": "取消团队任务",
    "send_input": "向研究员发送消息",
    "spawn_agent": "创建研究员",
    "apply_patch": "修改文件",
}
SHELL_LABELS = {
    "rg": "搜索文本",
    "grep": "搜索文本",
    "sed": "读取或修改文本",
    "head": "查看文件开头",
    "tail": "查看文件末尾",
    "cat": "读取或写入文件",
    "curl": "请求网络资源",
    "wget": "下载资源",
    "pip": "管理依赖",
    "python": "运行Python程序",
    "python3": "运行Python程序",
    "ls": "列出文件",
    "cp": "复制文件",
    "mkdir": "建立目录",
    "sha256sum": "核对文件哈希",
    "ps": "检查进程",
    "sleep": "等待",
    "pkill": "请求停止匹配进程",
    "kill": "请求停止进程",
    "diff": "比较文件",
    "wc": "统计文件行数",
    "jq": "筛选结构化数据",
}


def clipped(value, limit=220):
    text = str(value).replace("\x00", "")
    return text if len(text) <= limit else text[:limit] + "…〔节选〕"


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", "<br>")


def short_paths(value):
    """Display path aliases only, never resolve/execute supplied paths."""
    text = str(value)
    return re.sub(
        r'/[^\s\'"<>]+/(work|task)(?=/|[\s\'"<>]|$)',
        lambda m: "工作目录" if m.group(1) == "work" else "任务目录",
        text,
    )


def request_observation(tool, args):
    """Bounded, literal extraction. Shell patterns are hints, not parsed execution."""
    if not isinstance(args, dict):
        return dict(
            label=LABELS.get(tool, "调用工具"),
            details=["参数不是JSON对象，见原始记录"],
            keywords=[],
        )
    command = str(args.get("cmd", args.get("command", "")))
    details = []
    for key, label in [
        ("path", "文件"),
        ("destination", "保存到"),
        ("job_id", "作业"),
        ("record_id", "记录"),
        ("record_version", "记录版本"),
        ("kind", "类别"),
        ("task_id", "任务"),
        ("section", "目录"),
        ("target", "接收方"),
        ("goal", "任务目标"),
        ("purpose", "目的"),
        ("title", "标题"),
        ("scope", "范围"),
        ("status", "状态筛选"),
        ("offset", "偏移"),
        ("limit", "条数/字节上限"),
        ("after", "起始游标"),
        ("version", "版本"),
        ("minutes", "请求分钟"),
        ("summary", "摘要"),
        ("message", "消息"),
        ("decision", "决策"),
        ("reason_summary", "公开决策依据"),
        ("returned_summary", "Agent概括的返回"),
        ("important_findings", "Agent记录的重要知识及理由"),
        ("importance_status", "重要性记录状态"),
        ("decision_effect", "对下一步的影响"),
        ("outcome", "自报结果"),
        ("request", "查询条件"),
        ("chars", "发送给进程的内容"),
    ]:
        if key in args:
            details.append(label + "：" + clipped(args[key]))
    for key, label in [
        ("inputs", "输入文件"),
        ("outputs", "请求归档文件"),
        ("mechanisms", "提交机理"),
        ("argv", "执行入口"),
    ]:
        if isinstance(args.get(key), list):
            details.append(label + "：" + clipped("；".join(str(v) for v in args[key]), 350))
    if tool == "write_stdin":
        details.append("进程会话：" + str(args.get("session_id", "未提供")))
        if not args.get("chars"):
            details.append("未发送新输入，只等待/读取此前进程输出")
    keywords = []
    for url in re.findall(r'https?://[^\s\'"<>\\]+', command):
        query = parse_qs(urlsplit(url).query)
        for key in ("q", "query", "search"):
            for value in query.get(key, []):
                keywords.append(f"URL参数{key}={value}")
    label = LABELS.get(tool, "调用工具：" + str(tool))
    if command:
        # Inspect only shell command prefixes, never pretend to execute/understand a script body.
        shell_head = command.split("\n", 1)[0]
        details.append("实际命令入口（路径简写）：" + clipped(short_paths(shell_head), 260))
        names = re.findall(
            r"(?:^|[;&|]\s*|\b(?:timeout\s+\d+|nohup)\s+)\s*(?:\S*/)?([A-Za-z0-9_-]+)\b", shell_head
        )
        labels = list(dict.fromkeys(SHELL_LABELS[n] for n in names if n in SHELL_LABELS))
        if labels:
            label = "；".join(labels[:4]) + ("等操作" if len(labels) > 4 else "")
        # Expose literal file mentions; these are NOT a verified file access trace.
        paths = list(
            dict.fromkeys(
                re.findall(
                    r"[A-Za-z0-9_./-]+\.(?:py|jsonl?|csv|ya?ml|md|txt|log|npz|sh)\b", command
                )
            )
        )
        if paths:
            short = [p.split("/work/", 1)[-1] if "/work/" in p else p for p in paths]
            details.append(
                "命令提及文件（不等于全部已访问）：" + clipped("；".join(short[:10]), 450)
            )
        for fragment in re.split(r"[;\n]|&&|\|\|", command):
            if not re.match(r"^\s*(?:rg|grep)\s", fragment):
                continue
            try:
                tokens = shlex.split(fragment)
            except ValueError:
                continue
            # Keep exact expression, including flags, rather than guess positional option semantics.
            keywords.append("文本检索表达式：" + clipped(" ".join(tokens), 260))
    if not details:
        details.append(
            "无显式参数" if not args else "此工具参数未翻译；可展开实际参数，不推测操作目的"
        )
    return dict(
        label=label,
        details=details,
        keywords=list(dict.fromkeys(keywords)),
        interpretation="literal_syntax_hints_not_scientific_rationale",
    )


def output_observation(output):
    """Execution status is separate from returned text and scientific success."""
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    info = dict(status="工具已返回；科学是否成功未判定", signal=False, excerpt=clipped(text, 1600))
    code = re.search(r"(?m)^Process exited with code (-?\d+)\s*$", text)
    running = re.search(r"(?m)^Process running with session ID ([0-9]+)", text)
    if code:
        value = int(code.group(1))
        info.update(exit_code=value, signal=value != 0)
        info["status"] = f"本地进程退出码{value}" + (
            "（执行异常，需查原因）" if value else "（不等于科学验证通过）"
        )
    elif running:
        info["status"] = "进程仍在运行；会话" + running.group(1)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        if parsed.get("error_type"):
            info.update(status="工具报错：" + clipped(parsed["error_type"], 100), signal=True)
        fields = [
            (key, parsed[key])
            for key in (
                "status",
                "registered",
                "scientifically_verified",
                "message",
                "error_type",
                "job_id",
                "id",
                "version",
                "summary",
                "next_after",
            )
            if key in parsed
        ]
        if fields:
            info["summary"] = "；".join(k + "=" + clipped(v, 180) for k, v in fields)
        counts = [(k, len(v)) for k, v in parsed.items() if isinstance(v, list)]
        if counts:
            info["collection_sizes"] = dict(counts)
    if "Output:\n" in text:
        body = text.split("Output:\n", 1)[1]
        info["excerpt"] = clipped(body, 1600) or "〔此条返回没有正文〕"
    info["literal_preview"] = clipped(
        "；".join(line.strip() for line in info["excerpt"].splitlines()[:3]), 200
    )
    return info


def build(search, bundle=None):
    native = search["native"]
    sessions = native.get("sessions", [])
    names = {}
    researcher = 0
    for session in sessions:
        if session.get("parent_thread_id") or session.get("forked_from_id"):
            researcher += 1
            names[session.get("id")] = f"研究员{researcher}"
        else:
            names[session.get("id")] = "主Agent"
    prefix = (
        "S"
        if search["run_id"].startswith("solo")
        else "T" if search["run_id"].startswith("team") else "R"
    )
    refs = {}
    if bundle:
        for card in bundle["cards"]:
            for source in card["sources"]:
                refs.setdefault(source, []).append(card["id"])
        for query in bundle["queries"]:
            for source in query["sources"]:
                refs.setdefault(source, []).extend(query["cards"])
    items = [(r, "action") for r in native["actions"]] + [
        (r, "statement") for r in native["statements"]
    ]
    items.sort(key=lambda v: (v[0].get("at") or "", v[0]["source"], v[0]["line"]))
    rows = []
    prior_public = {}
    for number, (record, kind) in enumerate(items, 1):
        source = f"{record['source']}:{record['line']}"
        linked = set(refs.get(source, []))
        for output in record.get("outputs", []):
            linked.update(refs.get(f"{output['source']}:{output['line']}", []))
        row = dict(
            number=number,
            id=f"{prefix}{number:04d}",
            stable_id=sha256((kind + ":" + source).encode()).hexdigest(),
            kind=kind,
            at=record.get("at"),
            actor=record["actor"],
            actor_name=names.get(record["actor"], "会话身份未识别"),
            source=source,
            exact_source_card_links=sorted(linked),
            review_status="unreviewed",
        )
        if kind == "action":
            observation = record.get("request_observation") or dict(
                label=LABELS.get(record["tool"], "调用工具"),
                details=["旧快照缺少参数概括，按来源查看"],
                keywords=[],
            )
            row.update(
                tool=record["tool"],
                request=observation,
                request_excerpt=record.get("request_excerpt", ""),
                outputs=record.get("outputs", []),
                prior_public_step=prior_public.get(record["actor"]),
                rationale_status="not_explicitly_linked",
                label=observation["label"],
                execution_signal=any(o["observation"]["signal"] for o in record.get("outputs", [])),
            )
        else:
            row.update(label="公开说明", text=record["text"], execution_signal=False)
            prior_public[record["actor"]] = row["id"]
        rows.append(row)
    return dict(
        schema="observable-step-review.v1",
        contract_hash=search["state"].get("contract_hash"),
        run_root=search["run_root"],
        scope="all_exported_native_actions_and_public_statements",
        counts=dict(
            actions=len(native["actions"]),
            public_statements=len(native["statements"]),
            total=len(rows),
        ),
        coverage=search["coverage"],
        training_eligible=False,
        rows=rows,
    )


def documents(view):
    rows = view["rows"]
    positions = {r["id"]: (r["number"] - 1) // PAGE_SIZE + 1 for r in rows}

    def page_name(number):
        return f"STEP_PAGE_{number:03d}.md"

    def link(row):
        return f"{page_name(positions[row['id']])}#{row['id'].lower()}"

    files = {}
    pages = [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]
    index = [
        "# 每一步在做什么：完整执行目录",
        "",
        f"工具操作{view['counts']['actions']}条，公开说明{view['counts']['public_statements']}条，合计{len(rows)}步。没有过滤重复查询、等待、失败或重试。",
        "",
        "每次工具调用是一行/一步；同条命令内的脚本、循环和求解器内部步骤不拆成LLM决策。"
        "每页25步，工具返回放在对应操作下并标返回时间；并行步骤不能视为互相已知。"
        "调用记录时间不等于远端计算实际启动时间。",
        "",
        "中文操作概括按工具/命令语法提取，不推断科学动机。每条公开说明均保留，尚未逐条人工译成中文。"
        "“上一条公开说明”只提供同一Actor的背景，不自动等于本步理由。专家卡链接是事后索引，不是当时知识。",
        "",
        "[关键决策卡](EXPERIMENT_REPORT.md) · [全部执行异常信号](STEP_SIGNALS.md)",
        "",
        "|页|步骤范围|开始时间（UTC）|操作/说明数|执行异常信号数|",
        "|---|---|---|---:|---:|",
    ]
    for page_number, batch in enumerate(pages, 1):
        index.append(
            f"|[第{page_number}页]({page_name(page_number)})|{batch[0]['id']}—{batch[-1]['id']}|{batch[0]['at']}|{len(batch)}|{sum(r['execution_signal'] for r in batch)}|"
        )
        nav = "[总目录](STEP_BY_STEP.md)"
        if page_number > 1:
            nav += f" · [上一页]({page_name(page_number-1)})"
        if page_number < len(pages):
            nav += f" · [下一页]({page_name(page_number+1)})"
        lines = [
            f"# 逐步执行 · 第{page_number}页",
            "",
            nav,
            "",
            "|编号|时间（UTC）|执行者|做什么|",
            "|---|---|---|---|",
        ]
        for row in batch:
            lines.append(
                f"|[{row['id']}](#{row['id'].lower()})|{row['at']}|{row['actor_name']}|{cell(row['label'])}|"
            )
        for row in batch:
            lines += [
                "",
                f'<a id="{row["id"].lower()}"></a>',
                "",
                f"## {row['id']}｜{row['actor_name']}：{cell(row['label'])}",
                "",
                f"记录时间：{row['at']}（UTC）",
                "",
            ]
            if row["kind"] == "statement":
                lines += [
                    "Agent当时的公开说明（原文；不是事后补写，未经独立科学核验）：",
                    "",
                    cell(row["text"]),
                    "",
                ]
            else:
                lines += [
                    "|本步内容|记录|",
                    "|---|---|",
                    "|实际操作对象/条件|" + cell("\n".join(row["request"]["details"])) + "|",
                    "|查询关键词/表达式|"
                    + cell(
                        "\n".join(row["request"]["keywords"])
                        or "没有提取到显式检索表达式；不等于没有查询。实际参数可展开。"
                    )
                    + "|",
                ]
                previous = row["prior_public_step"]
                context = (
                    f"[查看{previous}]({page_name(positions[previous])}#{previous.lower()})（仅作背景，不证明这是本步理由）"
                    if previous
                    else "此前没有同一Actor的公开说明"
                )
                lines += [
                    "|最近一条同角色公开说明|" + context + "|",
                    "|本步理由/知识取舍|未逐条明确关联；不能从工具名反推。已核实解释见下方决策卡。|",
                ]
                if not row["outputs"]:
                    lines += ["|工具返回|在本次导出中未观察到返回；不推断成功或失败。|"]
                for output in row["outputs"]:
                    obs = output["observation"]
                    status = obs["status"]
                    if obs.get("summary"):
                        status += "；" + obs["summary"]
                    if obs.get("collection_sizes"):
                        status += "；返回集合条数：" + str(obs["collection_sizes"])
                    lines += [
                        f"|返回时间|{output['at']}（不提前算作调用提出时已知）|",
                        "|执行结果|" + cell(status) + "|",
                    ]
                    if obs.get("literal_preview"):
                        lines += [
                            "|返回正文开头（原样节选，不是Agent结论）|"
                            + cell(obs["literal_preview"])
                            + "|"
                        ]
                for output in row["outputs"]:
                    obs = output["observation"]
                    lines += [
                        "",
                        "<details><summary>展开：返回内容节选（原文，不是重要性判断）</summary>",
                        "",
                        cell(obs["excerpt"]),
                        "",
                        "</details>",
                        "",
                    ]
                lines += [
                    "",
                    "<details><summary>展开：实际工具参数（长参数为节选，完整见原始记录）</summary>",
                    "",
                    cell(row["request_excerpt"]),
                    "",
                    "</details>",
                    "",
                ]
            if row["exact_source_card_links"]:
                lines += [
                    "事后复盘索引（仅按明确来源链接）："
                    + "、".join(
                        f"[{c}](EXPERIMENT_REPORT.md#{c.lower()})"
                        for c in row["exact_source_card_links"]
                    ),
                    "",
                ]
            lines += [f"[本步原始记录]({Path(view['run_root']) / row['source']})", ""]
            for output in row.get("outputs", []):
                lines += [
                    f"[对应原始返回]({Path(view['run_root']) / output['source']}:{output['line']})",
                    "",
                ]
            lines += [f"你的意见可直接写：{row['id']}：……（本版本内定位；未回复保持未评审）", ""]
        lines += [nav, ""]
        files[page_name(page_number)] = "\n".join(lines)
    index += ["", "## 按执行者定位", ""]
    for actor in dict.fromkeys(r["actor"] for r in rows):
        actor_rows = [r for r in rows if r["actor"] == actor]
        index += [
            f"### {actor_rows[0]['actor_name']}（{len(actor_rows)}步）",
            "",
            "涉及页面："
            + "、".join(
                f"[{p}]({page_name(p)})" for p in sorted({positions[r["id"]] for r in actor_rows})
            ),
            "",
        ]
    if view["coverage"].get("sources_missing_or_limited") or view["coverage"].get(
        "unhandled_native_call_types"
    ):
        index += [
            "注意：原始来源存在缺失/截断或未解析类型，请查STEP_REVIEW.json中的coverage；不是整个运行的无缺失保证。",
            "",
        ]
    index += [
        "以上数量不是工况数、求解次数或模型调用次数。原始科学结果、预算、机理和评分未修改。",
        "",
    ]
    files["STEP_BY_STEP.md"] = "\n".join(index)
    signals = [
        "# 执行异常信号索引",
        "",
        "[完整目录](STEP_BY_STEP.md)",
        "",
        "只标出工具报错/进程非零退出，不等于化学方法失败。返回正文中的科学失败不保证自动识别；所有步骤仍在完整目录。",
        "",
        "|步骤|时间|执行者|操作|",
        "|---|---|---|---|",
    ]
    signals += [
        f"|[{r['id']}]({link(r)})|{r['at']}|{r['actor_name']}|{cell(r['label'])}|"
        for r in rows
        if r["execution_signal"]
    ]
    files["STEP_SIGNALS.md"] = "\n".join(signals) + "\n"
    return files
