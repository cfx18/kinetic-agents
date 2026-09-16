# 团队评测结束，但候选在入口被误拒：bulk/gas 格式兼容问题

检查时间：2026-09-15 14:22–14:25 UTC。
运行：team-20260915T131059.107037Z-e89a0b43；评测作业7950229。

## 当前结果是什么

独立评测已COMPLETE并回收scorecard，但实际只计算了父机理610个工况。
三份锁定候选在本地评测准备阶段被标记invalid_mechanism，未进入远端候选×工况列表。

| 对象 | 物种/反应 | 本次实际benchmark记录 | 结果 |
|---|---|---:|---|
| 父机理 | 111/784 | 610 | 610成功，mean(abs(signed_sigma))=2.6677088561391415 |
| candidate_1 | 54/392 | 0 | 入口ValueError，尚无误差 |
| candidate_2 | 56/402 | 0 | 入口ValueError，尚无误差 |
| candidate_3 | 69/461 | 0 | 入口ValueError，尚无误差 |

这个2.6677是标准化误差，不是百分比。候选coverage=0是入口拒绝的机器记录，不是做过610次求解后全部失败。
排行榜只有父机理，不构成父机理优于候选的有效科学比较。
调度回执：64核×835秒=14.8444分配核时；实际进程CPU约7.4078核时；均属独立评测账本。

## 已复现的根因

冻结的evaluation/artifacts.py中，inline_yaml把单一理想气体相的kinetics值硬限制为`gas`。
Agent使用Cantera 3.2.0导出三份候选，原文件均写`kinetics: bulk`。
因此在ct.Solution真正加载之前，就触发了误导性错误：

```text
ValueError: external phase/species/reaction references are not permitted
```

evaluation/submission.py只保存了异常类型ValueError，没有保存拒绝阶段和具体原因；
随后跳过候选，只把父机理纳入selected。最终总体COMPLETE只意味着该清单执行完，不意味着候选评测成功。

本轮用运行自身source-release中的原inline_yaml逐个复现同样拒绝，未修改冻结代码或机理。
同时直接用评测版本Cantera 3.0.1加载原始候选，三者均能成功读取，数量分别54/392、56/402、69/461。
所以这不是Cantera无法加载文件，也不是本轮已经测出了很大的宏观误差。

## 只读诊断的边界

在内存副本中将bulk写成gas后，三份机理的物种/反应数据一致；但Cantera 3.0.1返回的kinetics_model名称不同，
不能仅凭可加载就宣称所有版本、所有工况下二者无条件等价。
额外只做非积分速率诊断：三份机理在900K/1atm、1500K/10atm、2000K/30atm，使用全部保留物种等量混合，
正/逆/净反应进度速率及物种净生成率在两个内存版本间的最大绝对差均为0。
这支持兼容修复方向，但不是IDT/LFS实算，更不是完整物理等价证明。原锁定字节没有改写。
NASA拼接警告不是这次入口拒绝原因；本轮没有物理豁免。

## 单组及下一步

14:25:25核查单组仍RUNNING，无正式result.json；公开消息已经在整理81/87/101物种候选和最终报告。
自报误差/工况数未在本轮独立复算，不与团队统一benchmark并列比较，不向单组反馈团队评分。

大图景仍为强基线独立评测与可靠性核查。本轮新增的是评测器兼容缺陷证据，不是Agent科学失败或RSI收益。
下一步应修复并测试入口兼容/错误分类，以新评测修订记录对原锁定候选补评；
保留本次原scorecard及成本，不修改候选、不重开搜索、不改误差定义、不把修复后的结果冒充原版本运行。
本轮是状态查询及只读诊断，尚未改代码、启动补算、重置预算或跨越新的科学审查门。

证据：[scorecard.json](../runs/team-20260915T131059.107037Z-e89a0b43/team-max/endpoint/scorecard.json)、
[候选原文件](../runs/team-20260915T131059.107037Z-e89a0b43/team-max/endpoint/candidate_1.yaml)、
[冻结入口校验](../runs/team-20260915T131059.107037Z-e89a0b43/source-release/kinetic_agents/evaluation/artifacts.py)。
