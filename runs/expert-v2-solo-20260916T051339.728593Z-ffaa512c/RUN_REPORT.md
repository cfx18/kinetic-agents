# 实验总览

更新时间：2026-09-16T05:16:43.773489+00:00；状态：CANCELLED

模式：solo-max；主 Agent：gpt-6-astra / xhigh
Harness：codex；模型通道：subscription。
子 Agent：禁用 / —；最多同时 1 人（含主 Agent）。

全团队共用搜索预算：512.0 核时 / 24.0 小时；独立评测：64.0 核时。

## 结果与过程入口

| 内容 | 文件 | 状态 |
|---|---|---|
| 现场完整交互 | [solo-max/transcript.md](solo-max/transcript.md) | 已存在 |
| 机器可读交互 | [solo-max/transcript.jsonl](solo-max/transcript.jsonl) | 已存在 |
| 逐 Agent 交互 | [solo-max/agents](solo-max/agents) | 已存在 |
| 团队身份与用量 | [solo-max/native/subscription_team.json](solo-max/native/subscription_team.json) | 已存在 |
| API 请求与用量记录 | [solo-max/native/api_requests.jsonl](solo-max/native/api_requests.jsonl) | 尚未生成 |
| 子 Agent 状态与回复 | [solo-max/native/workers.json](solo-max/native/workers.json) | 尚未生成 |
| 工作中候选与脚本 | [solo-max/work](solo-max/work) | 已存在 |
| 搜索结果 | [solo-max/result.json](solo-max/result.json) | 已存在 |
| 锁定提交 | [solo-max/final_artifacts](solo-max/final_artifacts) | 尚未生成 |
| 独立评测状态 | [solo-max/endpoint/evaluation_state.json](solo-max/endpoint/evaluation_state.json) | 尚未生成 |
| 独立评测分数 | [solo-max/endpoint/scorecard.json](solo-max/endpoint/scorecard.json) | 尚未生成 |
| 中文复盘 | [solo-max/review/EXPERIMENT_REPORT.md](solo-max/review/EXPERIMENT_REPORT.md) | 已存在 |
| 逐步复盘 | [solo-max/review/STEP_BY_STEP.md](solo-max/review/STEP_BY_STEP.md) | 已存在 |
| 运行日志 | [solo-max/coordinator.log](solo-max/coordinator.log) | 已存在 |

## Agent 分工与交互

| 身份 | 模型 / 档位 | 实时记录 |
|---|---|---|
| 主 Agent `01a0a8a2-be76-7f33-a1b5-9a74eb2c52c9` | gpt-6-astra / xhigh | [transcript](solo-max/agents/01a0a8a2-be76-7f33-a1b5-9a74eb2c52c9/transcript.md) |

## 解释边界

- 混合模型对比不能单独归因于子 Agent 架构
- token 是服务报告的用量，不是美元账单；API 实际扣费以服务商为准，未配置美元上限
- 610 工况是历史已暴露评测池；搜索结束不等于评测完成
- 公开 I/O 不包含隐藏推理；缺失理由不补写

此页在准备/启动/收尾时自动更新；执行 `results --run` 可刷新。transcript 始终现场追加，不依赖此页刷新。
