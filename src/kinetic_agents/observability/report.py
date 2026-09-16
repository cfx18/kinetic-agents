"""Small human/agent entry point; not a new scorer or model-generated summary."""

import json
from pathlib import Path
from datetime import datetime
from datetime import timezone

from kinetic_agents.core.storage import atomic
from kinetic_agents.observability.review import plain


def publish(run, root, snapshot):
    run, root = plain(run), plain(root)
    plan = json.loads(plain(run / "preflight.json").read_text())
    contract = plan["contracts"][root.name]

    def link(path):
        path = plain(root / path)
        return dict(path=path.relative_to(run).as_posix(), exists=path.exists())

    paths = {
        label: link(path)
        for label, path in {
            "现场完整交互": "transcript.md",
            "机器可读交互": "transcript.jsonl",
            "逐 Agent 交互": "agents",
            "团队身份与用量": "native/subscription_team.json",
            "API 请求与用量记录": "native/api_requests.jsonl",
            "子 Agent 状态与回复": "native/workers.json",
            "工作中候选与脚本": "work",
            "搜索结果": "result.json",
            "锁定提交": "final_artifacts",
            "独立评测状态": "endpoint/evaluation_state.json",
            "独立评测分数": "endpoint/scorecard.json",
            "中文复盘": "review/EXPERIMENT_REPORT.md",
            "逐步复盘": "review/STEP_BY_STEP.md",
            "运行日志": "coordinator.log",
        }.items()
    }
    if (root / "endpoint_bulk_v1/repair_request.json").is_file():
        paths["原评测分数（保留的错误入口记录）"] = paths["独立评测分数"]
        paths["独立评测状态"] = link("endpoint_bulk_v1/evaluation_state.json")
        paths["独立评测分数"] = link("endpoint_bulk_v1/combined_scorecard.json")
        paths["补评中文结果"] = link("endpoint_bulk_v1/REPORT.md")
    agents = (snapshot.get("team") or {}).get("agents", {})
    value = dict(
        schema="research-run-overview.v1",
        updated_at=datetime.now(timezone.utc).isoformat(),
        status=snapshot["status"],
        mode=root.name,
        harness=contract.get("harness", {"name": "codex"}),
        backend=contract.get("backend", {"auth": "subscription"}),
        model=contract["model"],
        effort=contract["effort"],
        researcher_model=contract.get("researcher_model"),
        researcher_effort=contract.get("researcher_effort"),
        max_members=contract["max_members"],
        agents=agents,
        paths=paths,
        search_result=snapshot.get("result"),
        evaluation=snapshot.get("evaluation"),
        comparison_scope=contract.get("comparison_scope", "single_model_single_agent"),
        limits=[
            "混合模型对比不能单独归因于子 Agent 架构",
            "token 是服务报告的用量，不是美元账单；API 实际扣费以服务商为准，未配置美元上限",
            "610 工况是历史已暴露评测池；搜索结束不等于评测完成",
            "公开 I/O 不包含隐藏推理；缺失理由不补写",
        ],
    )
    atomic(run / "overview.json", value)
    lines = [
        "# 实验总览",
        "",
        f"更新时间：{value['updated_at']}；状态：{value['status']}",
        "",
        f"模式：{root.name}；主 Agent：{contract['model']} / {contract['effort']}",
        f"Harness：{value['harness']['name']}；模型通道：{value['backend']['auth']}。",
        f"子 Agent：{contract.get('researcher_model','禁用')} / {contract.get('researcher_effort','—')}；最多同时 {contract['max_members']} 人（含主 Agent）。",
        "",
        "全团队共用搜索预算："
        + str(contract["cpu_seconds"] / 3600)
        + " 核时 / "
        + str(contract["wall_seconds"] / 3600)
        + " 小时；独立评测："
        + str(contract["evaluation_cpu_seconds"] / 3600)
        + " 核时。",
        "",
        "## 结果与过程入口",
        "",
        "| 内容 | 文件 | 状态 |",
        "|---|---|---|",
    ]
    for label, item in paths.items():
        lines.append(
            f"| {label} | [{item['path']}]({item['path']}) | {'已存在' if item['exists'] else '尚未生成'} |"
        )
    lines += [
        "",
        "## Agent 分工与交互",
        "",
        "| 身份 | 模型 / 档位 | 实时记录 |",
        "|---|---|---|",
    ]
    for actor, row in agents.items():
        lines.append(
            f"| {'主 Agent' if row['parent'] is None else '子 Agent'} `{actor}` | {row['model']} / {row['effort']} | [transcript]({root.name}/agents/{actor}/transcript.md) |"
        )
    if not agents:
        lines += ["| 尚未启动 | — | — |"]
    lines += ["", "## 解释边界", ""] + ["- " + v for v in value["limits"]]
    lines += [
        "",
        "此页在准备/启动/收尾时自动更新；执行 `results --run` 可刷新。transcript 始终现场追加，不依赖此页刷新。",
        "",
    ]
    # This is an observational convenience file, not source evidence.
    target = plain(run / "RUN_REPORT.md")
    target.write_text("\n".join(lines), encoding="utf-8")
    return dict(
        status=value["status"],
        run=str(run),
        report=str(target),
        index=str(run / "overview.json"),
        paths=paths,
        model_calls=0,
        scientific_solves=0,
    )
