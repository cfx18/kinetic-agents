# 2026-09-15 独立评测入口修复与补评

## 问题与修复边界

原团队作业 7950229、单组作业 7950339 都仅评测了父机理。候选在 YAML 入口被拒绝，
不是候选在 610 个工况上求解失败，也不能据此认定物理不合法。

- 两组导出均使用 `kinetics: bulk`，旧检查硬编码为 `gas`。
- 单组另有 `design`、`parent` 两个说明字符串，旧检查也拒绝它们。
- 修复后仍要求单一 `ideal-gas` 相、内联物种和反应；仅支持 `gas/bulk` 及这两个
  最多 4096 字符的纯文本注释。未知顶层扩展、结构化注释和外部引用仍拒绝。
- 不改提交的任何字节，继续使用同版 Cantera 3.0.1 原生加载及不平衡反应拒绝检查。
- 入口错误新增 `failure_stage`、`reason_code` 和 `numerical_solves: 0`，与求解失败区分。

[Cantera 官方相定义](https://www.cantera.org/3.2/yaml/phases.html)明确说明 `gas` 是 `bulk` 的别名。
六个原文件均可直接加载；单组原文件与去掉说明字段的内存副本，其物种/反应数据和
900/1500/2000 K 非积分探针的速率一致。这只验证兼容性，不证明宏观精度或完整物化正确性。
已有 NASA 拼接警告保留，没有修改热力学数据或应用专家豁免。

## 原锁定提交

| 组别 | candidate_1 | candidate_2 | candidate_3 |
|---|---|---|---|
| Team | 54 物种 / 392 反应 | 56 / 402 | 69 / 461 |
| Solo | 101 / 542 | 87 / 513 | 81 / 464 |

父机理 111 物种 / 784 反应。每组补评 3 × 610 = 1,830 条候选—工况记录。
缺输入组分、数值失败、超时和漏算必须照实报告，不能用成功子集均值替代全池均值。

## 修订、资源与自动运行

每条原运行下新增 `solo-max/endpoint_bulk_v1/` 或 `team-max/endpoint_bulk_v1/`。
`endpoint/` 原结果、哈希、预算与失败记录不覆盖。只复用同一结束提交的原父机理610条记录，
不跨轨迹复用、不新增搜索、不把终点评分回流给 Agent。

| 组别 | 原评测分配核秒 | 原本地CPU秒 | 补评作业 | 新分配上限 | 含原消耗/本地预留总上界 |
|---|---:|---:|---|---|---:|
| Team | 53440 | 6.196994 | 7950590 | 1 节点 × 64 核 × 45 分钟 | 63.846166 核时 |
| Solo | 52736 | 5.584043 | 7950591 | 1 节点 × 64 核 × 45 分钟 | 63.650440 核时 |

每组原有64核时额度不重置。3600秒本地预留含最多3500秒独立所有者 CPU 与100秒准备/结算余量。
分配核时与实际进程CPU分开；评测不扣搜索预算；新模型调用为0。两个新作业名均含cfx，
只经 `ssh sca2070` / `sbatch` 提交，输出仅在：

- `/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_ca02e2d6c3b1457f9b99bb1a_endpoint_bulk_v1`
- `/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/cfx_astra_3a9fcc9a572e45bfb6a6fcb6_endpoint_bulk_v1`

后台所有者负责准备、提交、有限重试的只读接收、哈希验收、分数重算和父机理行合并。
聊天和 `status` 不驱动作业。提交意图唯一，不能盲目重发 `sbatch`；失败不清零账本。
未来人工恢复应先核验已有作业和成本，不能重复执行 `launch`。

冻结源码：`runs/evaluator-compatibility-fix-20260915/source-release`，93个源文件，
release SHA-256：`c8fdded2d3eb03d039222143fe2b8cb9fbd56da9d371706259fe4581b242f80a`。
原求解/评分源 pins、610池、实验观测及数值策略不变。

## 结果入口

- `endpoint_bulk_v1/acceptance.json`：原文件结构、哈希及原生加载验收。
- `endpoint_bulk_v1/evaluation_state.json`：提交/接收状态。
- `endpoint_bulk_v1/observation.json`：后台获取的调度/进度快照；按时间判断新旧。
- `endpoint_bulk_v1/combined_scorecard.json`：完成后，父机理与三候选的同口径评分、覆盖及分组误差。
- `endpoint_bulk_v1/combined_result.json`：全部原始逐case结果及按候选标识的来源。
- `endpoint_bulk_v1/REPORT.md`：自动生成的中文结果表。
- `endpoint_bulk_v1/local_cpu.json`、`owner_finished.json`：本地所有者资源与退出原因。

`run.py status/results/review --run <原时间戳运行目录>` 已识别补评修订，同时保留原评测入口。
补评完成前不会把原父机理单独排名当成候选的最终成绩。原搜索交互和提交未修改。

## 验收与证据边界

最终全量回归226通过/11跳过（28.58秒），修订模块9项通过。
覆盖两种动力学名称、说明字段、未知扩展/外部引用/符号链接拒绝、不平衡反应、锁定字节保护、
跨修订预算、同提交原始行来源、重复提交拒绝、双组作业ID隔离，以及报告读取新评分。
本机项目盘（Ceph挂载）发生过文件访问长阻塞，恢复后全量测试29.88秒完成；未定位存储内部原因。

此轮仍为强基线统一评测。610池历史已暴露，不是新盲测；只有一组配对且团队使用异构子模型，
不能单独归因于子Agent架构，不证明RSI。60/40新grader仍待审查，本轮不启用。
作业提交后新增的科学结果另记录，本文不预判两组谁更好。
