# 实验总览

更新时间：2026-09-16T05:16:44.735245+00:00；状态：CANCELLED

模式：team-max；主 Agent：gpt-6-astra / xhigh
Harness：codex；模型通道：subscription。
子 Agent：gpt-5.5 / high；最多同时 3 人（含主 Agent）。

全团队共用搜索预算：512.0 核时 / 24.0 小时；独立评测：64.0 核时。

## 结果与过程入口

| 内容 | 文件 | 状态 |
|---|---|---|
| 现场完整交互 | [team-max/transcript.md](team-max/transcript.md) | 已存在 |
| 机器可读交互 | [team-max/transcript.jsonl](team-max/transcript.jsonl) | 已存在 |
| 逐 Agent 交互 | [team-max/agents](team-max/agents) | 已存在 |
| 团队身份与用量 | [team-max/native/subscription_team.json](team-max/native/subscription_team.json) | 已存在 |
| API 请求与用量记录 | [team-max/native/api_requests.jsonl](team-max/native/api_requests.jsonl) | 尚未生成 |
| 子 Agent 状态与回复 | [team-max/native/workers.json](team-max/native/workers.json) | 已存在 |
| 工作中候选与脚本 | [team-max/work](team-max/work) | 已存在 |
| 搜索结果 | [team-max/result.json](team-max/result.json) | 已存在 |
| 锁定提交 | [team-max/final_artifacts](team-max/final_artifacts) | 尚未生成 |
| 独立评测状态 | [team-max/endpoint/evaluation_state.json](team-max/endpoint/evaluation_state.json) | 尚未生成 |
| 独立评测分数 | [team-max/endpoint/scorecard.json](team-max/endpoint/scorecard.json) | 尚未生成 |
| 中文复盘 | [team-max/review/EXPERIMENT_REPORT.md](team-max/review/EXPERIMENT_REPORT.md) | 已存在 |
| 逐步复盘 | [team-max/review/STEP_BY_STEP.md](team-max/review/STEP_BY_STEP.md) | 已存在 |
| 运行日志 | [team-max/coordinator.log](team-max/coordinator.log) | 已存在 |

## Agent 分工与交互

| 身份 | 模型 / 档位 | 实时记录 |
|---|---|---|
| 主 Agent `01a0a8a2-c535-7c31-8a0f-13c1872cbe8a` | gpt-6-astra / xhigh | [transcript](team-max/agents/01a0a8a2-c535-7c31-8a0f-13c1872cbe8a/transcript.md) |
| 子 Agent `01a0a8a3-b6cf-76f0-8712-19ba36d73622` | gpt-5.5 / high | [transcript](team-max/agents/01a0a8a3-b6cf-76f0-8712-19ba36d73622/transcript.md) |
| 子 Agent `01a0a8a3-be10-7bd1-9fab-bd88c7117df9` | gpt-5.5 / high | [transcript](team-max/agents/01a0a8a3-be10-7bd1-9fab-bd88c7117df9/transcript.md) |

## 解释边界

- 混合模型对比不能单独归因于子 Agent 架构
- token 是服务报告的用量，不是美元账单；API 实际扣费以服务商为准，未配置美元上限
- 610 工况是历史已暴露评测池；搜索结束不等于评测完成
- 公开 I/O 不包含隐藏推理；缺失理由不补写

此页在准备/启动/收尾时自动更新；执行 `results --run` 可刷新。transcript 始终现场追加，不依赖此页刷新。
