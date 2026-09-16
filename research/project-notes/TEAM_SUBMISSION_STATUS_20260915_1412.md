# 团队正式提交与独立评测状态（2026-09-15 14:12 UTC）

团队运行：team-20260915T131059.107037Z-e89a0b43。

## 已确认

主Agent已正式提交最终机理，而不仅是子Agent提交任务报告。
运行终态COMPLETED，submission.outcome=submitted；result.json记录14:04:28 UTC完成，
从13:11:01启动到结束约53分27秒，早于原24小时截止时间。

从final_artifacts读取锁定字节、校验SHA-256后，用Cantera仅解析结构（没有求解）：

| 机理 | 物种 | 反应 | 物种减少 | 反应减少 |
|---|---:|---:|---:|---:|
| USC-II父机理 | 111 | 784 | — | — |
| usc2_54sp.yaml | 54 | 392 | 51.35% | 50.00% |
| usc2_56sp.yaml | 56 | 402 | 49.55% | 48.72% |
| usc2_69sp.yaml | 69 | 461 | 37.84% | 41.20% |

以上复杂度是已核实的结构事实，不等于精度或物理约束均已独立通过。
解析时Cantera报告部分NASA多项式拼接不连续警告，父机理读取也出现同类警告；本轮未修改热力学参数或给予物理豁免。

## 独立评测

自动提交的Slurm作业7950229，名称cfx_astra_ca02e2d6c3b1457f9b99bb1a_endpoint。
本轮通过ssh sca2070只读squeue/sacct确认RUNNING，1节点64核，查询时已运行约7分20秒。
本地evaluation_state仍显示SUBMITTED，这是提交回执，不是最新调度状态；不能据此说还没开算。
远端根目录为：

```text
/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_ca02e2d6c3b1457f9b99bb1a_endpoint
```

仍在用户指定Caifeixue下；终点评测与搜索目录、资源账本分开，不扣Agent搜索核时。
当前没有本地最终scorecard。result.scientifically_verified=false，不应把搜索COMPLETED当作独立科学评测已通过。

## 自评与证据边界

锁定REPORT.md自报：99个自建训练工况、108个自留验证工况，并报告相对父机理的IDT/LFS误差。
本轮没有重算或全面审计这些值，不将它们当作项目统一benchmark分数、未见盲测或RSI效果证据。
尤其不能因为54物种就宣称优于人类方案或单Agent；需等待相同评测定义下的结果。

本轮只读状态/提交/结构及调度查询，没有重新提交、取消、修订机理或向搜索反馈评分。
大图景进入强基线锁定成果的独立评测阶段；无新增科学审查点，下一步是评测完成后的准确率、覆盖率与失败分析。

原始提交回执：[submission.json](../runs/team-20260915T131059.107037Z-e89a0b43/team-max/submission.json)。
执行与评测状态：[result.json](../runs/team-20260915T131059.107037Z-e89a0b43/team-max/result.json)、
[evaluation_state.json](../runs/team-20260915T131059.107037Z-e89a0b43/team-max/endpoint/evaluation_state.json)。
