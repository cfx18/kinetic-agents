# 两组如何自建验证集：FFCM 检索与选点复盘

日期：2026-09-15。只读检查公开 Agent 说明、工具请求、选点脚本和最终报告；未启动求解或模型调用，未改任务契约、候选、评分或已结束搜索。

## 结论

两组主要自建的是“子机理对父机理的保真度验证集”，不是以公开实验观测为主体的 benchmark。
它们确实查了实验数据库，但实验点被安排为独立辅助核查，没有成为主要选点和删减决策的依据。
这是证据和任务操作化方式的区别，不表示自建数值工况无效或实验数据被伪造。

FFCM-2/SFCPD 提供了可借鉴的实验数据库组织方式；本次保存的主动检索、工具请求和来源记录未见两组访问该数据库。
Solo 主 transcript 无 FFCM/SFCPD 匹配。Team 中 FFCM-1 网址仅见于 Crossref 返回的其他文献参考文献片段；没有找到随后的主动访问。
这些只说明保存的外显行为，不证明模型内部不知道 FFCM，也不证明访问该站受到网络阻断。

## 实际决策顺序

### 补充核验：小燃料/2–4点是谁写的？

直接核对 Team 主会话初始输入（原生 rollout 第7行）与第一次委派（第19行）：

- 我们注入的科学任务写的是 `across chemically diverse conditions`、`Decide which cases and measurements are informative`，未限定小分子、小燃料、上述燃料枚举、2–4实验点或20分钟文献任务。
- 团队运行协议确实要求 `bounded independent work`、共享总预算和相关证据最小传递。这是一般委派边界，不指定化学范围或实验数量；不能排除其影响任务粒度，但没有因果证据。
- `chemically diverse small fuels (H2/CO/CH4/C2H4/C2H6/C3H8, optionally C2H2)`、`provide 2-4 useful benchmark conditions` 和 `Prefer completion in ~20min` 均出现在主 Agent 自己生成的 `research_spawn(name="literature", message=...)` 参数。
- 同一批工具调用中，它给 audit 子 Agent 的工况设计任务也枚举了 H2/CO/CH4/C2/C3H8及混合物；两条子任务都被主 Agent 收窄，而不是文献子 Agent 独自偏离明确的全域委派。
- 初始 base_instructions 和动态工具定义中也未匹配到上述小燃料/点数要求；本地TASK全文与会话实际收到的科学任务一致。

因此应区分：对文献子 Agent 而言，是上游主 Agent 的提示要求；对整个实验而言，不是我们手写了“小燃料、2–4点”，而是主 Agent 的规划选择。我们的任务未明确定义“跨化学工况”的最低覆盖要求，是另一层契约模糊性，不能据此断言提示与结果完全无关。

### Team：先限定小燃料数值域，文献子任务只找少量外部锚点

- 13:11:48，主 Agent 的公开工具请求把 literature 子任务限定为 H2/CO/CH4/C2H4/C2H6/C3H8、可选 C2H2，要求可靠提取 **2–4 个**实验点，建议约 20 分钟内完成，禁止子任务模拟/安装；数值工作由主 Agent 负责。
- 13:12:34，主 Agent 明确知道父机理包含 C4 和芳香烃，但表示会声明较小机理所保留的适用域。
- 13:17:34，已宣布 99 训练工况、108 自留验证工况，目标为相对父机理 IDT 最大误差 10%、火焰速度最大误差 5%。
- 13:24:57，才公开汇报找到四种燃料的激波管实验值，并明确作为与父机理预测分开的对照。

已保存的真实检索例子：

- DuckDuckGo：`ReSpecTh laminar burning velocity methane XML`。
- Crossref：`laminar burning velocity methane air table`。
- Crossref：`Comparison of methane combustion mechanisms using laminar burning velocity measurements`。
- 文献子 Agent 找到 Stanford 激波管数据库，以及 ReSpecTh 的元数据接口；其记录称当时若干原始 XML/ZIP 路径返回空内容或应用页面，不应推广为这些资源普遍不可获取。

选点脚本 `work/scripts/workflow.py:22`：

- 九种燃料组成：H2、H2/CO、CH4、C2H2、C2H4、C2H6、C3H6、C3H8、CH3OH。
- 训练 IDT：1000/1250/1600 K，1/10/30 atm，phi 0.5/1/2，按循环配对取每燃料 9 点，不是完整三维笛卡尔积；共 81 点。
- 训练火焰：每燃料 300 K、1 atm、phi 0.7/1.4，共 18 点。合计 99。
- 自留验证：改变温压/当量比并加入三种混合燃料，共 78 IDT + 30 火焰 =108。
- `wet_syngas` 的真实组成为 CO/H2/CO2，不含 H2O，不能按名字称为水蒸气稀释验证。
- 实验核查另有 Stanford 数据库 4 个 IDT 点、同一甲烷文献 3 个压力反演燃烧速度点；后者不是无歧义的直接平面 LFS 测量，部分 IDT 定义也用了代理。它们不是主优化训练集，未获得完整实验不确定度。

### Solo：先覆盖更广燃料域，再用验证失败扩大检查和修复

- 13:12:42，主 Agent 宣布覆盖氢气、合成气、含氧燃料、饱和/不饱和烃及芳香烃；同样以相对父机理的 10% IDT /5% 火焰误差为初始严格目标，另设较宽松压缩档。
- 真实 Google 检索：`ReSpecTh methane laminar flame speed experimental xml`；之后通过 GitHub API 查找 ReSpecTh/ChemKED，读取 ChemKED 甲苯 YAML 和原始 NASA 报告。
- `make_cases.py`：15 种燃料组成，含正/异丁烷、苯和甲苯；每种 3 个训练 IDT、1 个训练火焰，合计 60 训练点。另有 83 个初始验证点，含四种混合燃料。
- `make_challenge.py`：再加 44 点，包括随机温压/当量比、高压火焰及 H2O/CO2 稀释；此前验证总数成为 127。
- 原验证出现苯点火退化后，Agent 恢复通路，并通过 `make_confirmation.py` 再生成 38 个确认点。
- 最终父机理保真度池为 **225 =60+127+38**，其中 157 IDT、68 火焰。127 个原验证点已反馈到修复，不能称为最终修订的独立留出集；38 个新增确认点的冻结情况按 Agent 报告与保存脚本描述，不等于外部盲测。
- 实验核查另有 **12 个甲苯 IDT**，不用于物种选择/速率拟合。Agent 核对原文后报告 ChemKED 点火定义与原文不一致，显式区分压力起升估计与最大压力导数；没有直接实验火焰速度验证。

上述数量是工况数，不是实验论文数，也不是候选×工况求解调用数。辅助灵敏度/数值收敛作业不重复计为独立 benchmark。

## FFCM-2 能提供什么

官方 SFCPD 说明：评估 342 篇研究文章及既有数据库，覆盖 C0–C4，组织火焰速度、高温激波管点火、组分时间历程及部分流动反应器/稳定火焰数据；明确不纳入本轮低于 1000 K 的激波管点火及 RCM 数据。选出 1192 个优化 target，并把部分混合燃料和 N2 稀释数据用于额外测试。

验证页面按燃料/观测类型列条件、混合物摩尔分数、实验方法和点火定义，并说明图中误差棒为两倍标准差。这提供的是“来源—工况—观测定义—不确定度—用途”的组织范式，不是下载网页后自动获得适配本任务的干净盲测包。优化 target 也不自动等于统计独立样本或未见测试。

- https://web.stanford.edu/group/haiwanglab/FFCM2/docs/ExperimentalData/SFCPD/
- https://web.stanford.edu/group/haiwanglab/FFCM2/docs/Results/Validation/

## 可解释的失败模式与边界

共同 TASK.md 要求跨化学工况压缩、发现公共资源、自主选点、区分实验和父机理，但没有强制特定燃料清单、实验数据库主导选点或 FFCM 访问。
因此不能称 Team 违反了明确 C4 条款，也不能把这种收窄说成 harness 硬编码要求。公开轨迹支持：主 Agent 自行把实验资料设为辅助任务，保真度筛选则由自建数值域主导。

这能解释 Team 为什么能在自留 108 点误差小，却在独立 610 历史暴露池出现 57 个输入不兼容：两个域不同。不能证明若访问 FFCM 就一定消除缺口，或单次差异来自子 Agent/模型/记忆架构的因果效应。

后续值得讨论的是要求 Agent 展示：公开资源发现→目标燃料/观测覆盖表→工况入选理由→实验与父机理双重误差→未覆盖域。可保留自主决定来源和计算点，不必预先喂 610 池。该建议尚未写入任务或启动新实验。

## 可核查本地来源

路径相对 `kinetic_agents/`：

- 共同任务：`tasks/usc_ii/TASK.md`。
- Team：`runs/team-20260915T131059.107037Z-e89a0b43/team-max/`。
- Solo：`runs/solo-20260915T131056.265011Z-108d5c07/solo-max/`。
- 各组 `work/REPORT.md`、`work/inputs/`、`work/scripts/`、公开 `transcript.md`。
- Team `review/versions/9c28437208490cde6b4a2e663e56fb7dda9880c0909536729c738cc13ddf3733/STEP_REVIEW.json`：T0003/T0005/T0012/T0038。
- Solo `review/versions/e24f9e7ca0ec046a1943df14b679cc9f5979d3a8944f1808f30ff698c935b85e/STEP_REVIEW.json`：S0007/S0026/S0042/S0112/S0121。
- Team 文献公开工具流：`native/workers/literature/codex/sessions/2026/09/15/rollout-2026-09-15T13-11-55-01a0a532-0b4d-7aa2-9ffb-b2b1c788bd86.jsonl`，74/770/771 行为检索请求；只检查公开调用，不提取私有推理。

当前位置：强基线科学行为/验证域失败模式审计，未形成 RSI、规模 PDE 迁移或论文主张。无新审批事项；改变任务科学域或启动新实验时另按项目审查规则执行。
