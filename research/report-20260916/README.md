# 自主机理压缩：飞书导入材料

建议先打开 `自主机理压缩_理论与实验结果_20260916.docx`；该文件含中文正文、16 张表和 4 幅图，可作为飞书导入文档。PDF 用于固定排版阅读，HTML 是内嵌图片的单文件版本，Markdown 用于继续编辑。

本包为内部工作稿，不是在线飞书链接，也未发布给外部人员。

## 内容

- 理论：跨工况宏观误差—复杂度目标；LLM、Harness、状态、记忆、技能与 RSI 的边界；代理模型和主动实验设计的研究设想。
- 实验：普通 / 专家任务 × Solo / Team × GPT / Kimi / DeepSeek，12 个单元，其中 10 个已核实终点评分。
- 结果：30 个候选、18,300 条候选×工况记录，10 个候选完整覆盖固定 610 工况；其他候选保留失败状态，不伪造全池均值。
- 复盘：分组误差、输入域缺失、自验证盲区、方法实现问题、任务停止与基础设施故障。
- 边界：单次轨迹、历史已暴露工况、模型与 Harness 混淆、过程审计 / 完整成本未重新取齐；不主张 RSI 因果收益或普遍模型排名。

## 文件

- `自主机理压缩_理论与实验结果_20260916.docx`：20 页导入 / 编辑版。
- 同名 `.pdf`：20 页固定排版版。
- 同名 `.html`：图片嵌入，可离线阅读。
- 同名 `.md` 与 `figures/`：编辑源与四张图。
- `candidate_results.csv`：全部候选汇总。
- `case_results.csv`：逐工况预测、误差、状态和来源。
- `fuel_observable_results.csv`：分组误差和覆盖。
- `results.json`、`error_contributions.json`、`endpoint_allocation_costs.json`：机器可读分析。
- `evidence/`：已完成远端评测的 JSON 快照；保留了两个修复前失败版本和对应修复版本。JSON 是格式化快照，不宣称与原文件逐字节一致。
- `collect_remote.py`：只读收集脚本；无模型调用、无作业提交。
- `analyze_results.py`：从证据重算表与图。
- `build_document.py`、`report_template.md`：生成文档的源。
- `PENDING_PROJECT_UPDATES.md`：等待 Ceph 恢复后同步的状态和决策记录。
- `verification.json`：数据及文档验收摘要。
- `manifest.json`：交付文件校验值，不等于防篡改历史证明。

## 本轮验收

逐候选核对 610 个唯一工况记录、成功数、从 prediction / observation 重算的 signed_sigma、成功子集均值。10 组的工况内容规范化哈希与科学 source pins 一致。DOCX ZIP 结构通过检查，PDF 导出成功，并抽查了正文、主成绩表、覆盖图与分组误差图的排版。

## 存储说明

项目 Ceph 路径在本轮阻塞，文档存放在 `/root/agenticrl-deliverables/20260916-feishu-review`，未假装同步回项目。没有修改 / 重启正在运行的研究轨迹。没有付费 LLM 评审，也没有启动新科学计算。

报告里的“历史过程记录”与“本轮重核终点”分别标注。旧 Wang 对照未在本轮重新取得原文件，不用于当前统一排名。
