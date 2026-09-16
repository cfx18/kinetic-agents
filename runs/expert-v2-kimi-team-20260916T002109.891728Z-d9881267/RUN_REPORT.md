# 实验总览

更新时间：2026-09-16T05:03:38.104995+00:00；状态：COMPLETED

模式：team-max；主 Agent：kimi-k3 / max
Harness：kimi_code_node；模型通道：api。
子 Agent：kimi-k3 / max；最多同时 3 人（含主 Agent）。

全团队共用搜索预算：512.0 核时 / 24.0 小时；独立评测：64.0 核时。

## 结果与过程入口

| 内容 | 文件 | 状态 |
|---|---|---|
| 现场完整交互 | [team-max/transcript.md](team-max/transcript.md) | 已存在 |
| 机器可读交互 | [team-max/transcript.jsonl](team-max/transcript.jsonl) | 已存在 |
| 逐 Agent 交互 | [team-max/agents](team-max/agents) | 已存在 |
| 团队身份与用量 | [team-max/native/subscription_team.json](team-max/native/subscription_team.json) | 已存在 |
| API 请求与用量记录 | [team-max/native/api_requests.jsonl](team-max/native/api_requests.jsonl) | 已存在 |
| 子 Agent 状态与回复 | [team-max/native/workers.json](team-max/native/workers.json) | 已存在 |
| 工作中候选与脚本 | [team-max/work](team-max/work) | 已存在 |
| 搜索结果 | [team-max/result.json](team-max/result.json) | 已存在 |
| 锁定提交 | [team-max/final_artifacts](team-max/final_artifacts) | 已存在 |
| 独立评测状态 | [team-max/endpoint/evaluation_state.json](team-max/endpoint/evaluation_state.json) | 已存在 |
| 独立评测分数 | [team-max/endpoint/scorecard.json](team-max/endpoint/scorecard.json) | 已存在 |
| 中文复盘 | [team-max/review/EXPERIMENT_REPORT.md](team-max/review/EXPERIMENT_REPORT.md) | 已存在 |
| 逐步复盘 | [team-max/review/STEP_BY_STEP.md](team-max/review/STEP_BY_STEP.md) | 已存在 |
| 运行日志 | [team-max/coordinator.log](team-max/coordinator.log) | 已存在 |

## Agent 分工与交互

| 身份 | 模型 / 档位 | 实时记录 |
|---|---|---|
| 主 Agent `97e6f10b-ffd3-4f15-8cb0-c21ed69e3a25` | kimi-k3 / max | [transcript](team-max/agents/97e6f10b-ffd3-4f15-8cb0-c21ed69e3a25/transcript.md) |
| 子 Agent `bffec5cc-374c-43bf-8d10-f7f2dc87177b` | kimi-k3 / max | [transcript](team-max/agents/bffec5cc-374c-43bf-8d10-f7f2dc87177b/transcript.md) |

## 解释边界

- 混合模型对比不能单独归因于子 Agent 架构
- token 是服务报告的用量，不是美元账单；API 实际扣费以服务商为准，未配置美元上限
- 610 工况是历史已暴露评测池；搜索结束不等于评测完成
- 公开 I/O 不包含隐藏推理；缺失理由不补写

此页在准备/启动/收尾时自动更新；执行 `results --run` 可刷新。transcript 始终现场追加，不依赖此页刷新。
