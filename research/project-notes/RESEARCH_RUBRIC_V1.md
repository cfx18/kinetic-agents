# 六维科研质量 Rubric V2：离线模块与评分草案

文件名保留以兼容已有链接；当前配置为`research-rubric.v2`，历史五维V1证据包不覆盖，其离线评语仍可校验。

状态：用户选择B，仅实现模块/草案，不调用模型，不自动试评。当前不是已经完成的Codex+Astra评审器接入，也没有任何真实项目的新rubric分数。

## 为什么加这一层

数值评测回答：机理小了多少，在固定工况上的误差、覆盖与失败如何。Rubric回答：是否选了值得解决的问题、方法是否合理、证据是否足够、结果是否稳健、有无受证据支持的新贡献。两者互补，不让流畅文笔抵消化学错误或缺失工况。

| 维度 | 核心问题 | 不应奖励的东西 |
|---|---|---|
| Taste | 科研判断：应用域、瓶颈、信息价值、压缩/精度/成本取舍与停止是否合理？ | 漂亮图表、篇幅、研究口号 |
| Methodology | 方法、指标、数据定义、对照、验证分离和工具正确性设计是否合理？ | 单纯采用复杂算法或调用很多工具 |
| Novelty | 相对提供且核实的文献/基线，贡献是什么、是否有用且得到验证？ | 自称首创、常规方法改名、只因“Agent自主写了代码”加分 |
| Solid | 当前主张有多少可追溯、可复算、互相一致的真实证据？ | 只看作者总结、把求解成功当实验正确 |
| Robust | 在声称应用域内，换工况/数值设置/边界后是否稳定，失败是否界定清楚？ | 把重复算相似点等同跨域稳定性 |
| DecisionQuality | 当时掌握的信息是否支持行动？选点、复用、修复、调整与停止是否合理且高效？ | 以成败倒推理性、把排队算作思考、认为范围更广但更慢就是低效 |

每维0–4的明确锚点在`configs/rubric.yaml`；不求和、不加权、没有总排名。通常0表示存在严重相反证据，1弱，2基本合理，3强，4非常充分；Novelty中的1可表示可靠常规方法，不代表科研任务失败。证据不足为NA/null，不是0；同时报告证据缺口，不能用NA隐藏失败。置信度low/medium/high只是评审者序数，不是统计置信区间。

Methodology评设计，Solid评现有证据，Robust评跨条件稳定性，DecisionQuality评实际决策序列及效率。一个缺陷可以影响多个维度，但必须解释不同后果，不能机械重复扣分。严格区分用户原任务范围、Agent缩窄范围和独立评测范围；不把事后评测案例倒推成提示词明示要求。未要求3D的任务不因没有3D自动扣分。

Rubric YAML中的问题、锚点、规则及生成的`prompt.md`全部用英文；中文仅用于人类报告、输出字段值及原始证据。`account.auth_home`固定为`/root/.codex-experiment`，禁止API/当前账号回退；只规定未来执行身份，不代表已经调用或实测了评审模型。账户路径保留在Host配置，模型提示不含此路径，不复制账号凭据。

## 模块结构

```text
configs/rubric.yaml             六维英文问题、0–4锚点、规则、专用账号/拟用模型身份
evaluation/rubric/packet.py     冻结只读证据、来源分类、容量/路径/哈希检查
evaluation/rubric/service.py    catalog/search/read/submit接口及实时工具IO记录
evaluation/rubric/schema.py     严格JSON结构、实际引用核验、中文报告
```

三个边界：

1. **Host掌握真实证据。** 输入为原任务、锁定提交报告/机理、明确选择的独立endpoint汇总及逐case原始预测/误差/状态/诊断、父机理参考、搜索脚本/结果/文本来源、已有公开决策快照及资源记录。核对scorecard中的原始结果哈希；只有汇总或不匹配时拒绝准备。历史修复endpoint只有候选时显式加入原endpoint父参考，不假造统一来源。不读凭据目录、原始账号会话、其他运行或私有benchmark输入包。中间科学材料标为未经独立认证的search_artifact。
2. **Judge按需查证。** `catalog`分页给证据类别与完整度，`search`记录字面关键字和命中位置，`read`按行读原文，`submit`引用实际读过的行。缺少对原任务、评测汇总/逐case和父参考的实际读取时，服务拒绝提交；这只是最低读取门，不能证明完整阅读或理解。不同模型/Harness后续实现同一个`JudgeTransport`协议；当前没有生产transport实现。未来只给本评审包和工具，不挂载原仓库或其他轨迹。
3. **程序核验格式与出处，人核验解释。** 程序拒绝越界分数、漏维度、虚构原文、未读取的引用、无文献引用却给Novelty分数。DecisionQuality有分数时必须同时引用决策记录与实际独立评测，并给出决策审计。程序不能证明引用逻辑上支持结论、文章真实性或全球首创。高分仍需人类专家抽查。

## 已实现的离线入口

运行结束、锁定提交且独立endpoint生成后，可在`kinetic_agents/`下执行（不调用模型）：

```bash
../.venv-usc-official-compatible/bin/python run.py rubric-prepare \
  --run runs/<运行目录> --config configs/rubric.yaml
```

历史修复评测必须显式选择，不自动选对候选最有利的版本：

```bash
../.venv-usc-official-compatible/bin/python run.py rubric-prepare \
  --run runs/<运行目录> --config configs/rubric.yaml \
  --endpoint endpoint_bulk_v1/scorecard.json
```

产出在`runs/<运行目录>/rubric/<时间戳>/`，不进入Agent的work目录：

- `packet.json`、`documents/`：冻结的规范化文本、证据类别、遗漏与限制。
- `host-provenance.json`：仅Host保留原路径/哈希映射，不传给未来Judge。
- `prompt.md`、`output-schema.json`：固定评审输入和输出契约。
- `PREPARED.json`：明确`model_calls=0`、`live_execution_enabled=false`。

离线校验显式提供的JSON，并生成中文报告：

```bash
../.venv-usc-official-compatible/bin/python run.py rubric-check \
  --run runs/<运行目录>/rubric/<时间戳> --response <review.json>
```

这不是模型调用。结果来源标记`unverified_import`；仅确认结构/出处，没有证明模型身份或语义正确性。每次校验新建`assessments/`子目录，不覆盖旧评语。内部`EvidenceService`额外要求引用曾实际通过read读取；所有成功查询与提交即时写入`transcript.md/jsonl`，便于查“怎么搜索、看到什么”。

每维输出：分数或NA、置信度、中文依据、证据ID/行号/原文、局限、下一检查。另有整体中文总结和关键问题；没有隐藏思维链字段，不推测未记录的理由。

决策审计逐项保存：当时已知信息→行动与替代选择→实际获得的反馈/调整→效率→独立评测结果→事后归因限制。决策、反馈、结果分别引用；没有反馈记录就明确未知，不编造。独立评测用于核实效果，不能假装它在搜索阶段可见。效率必须结合任务覆盖，并区分模型等待、排队、分配核时与进程CPU。

## 证据包限制

- 文本单文件最多4MiB、总体64MiB、最多8000条；上调文件数适配Solo的大量小型逐case文件，但不扩大字节上限。目录默认40条、最多80条一页，支持按证据类别过滤，绝不把完整目录/历史直接注入上下文。超限/二进制/解析问题明确记录，不静默猜内容。关键任务/提交/endpoint不能完整捕获时拒绝准备。读取最多80行/64KB，超出要求缩小范围，不默默截断；搜索摘要若截断显式标注。
- 不自动导入PDF、图片、压缩包、完整原生会话；可用已保存文本，但必须披露来源和转换限制。已有NOTEBOOK/公开时间线可导入；没有则明确缺失。
- 不把匿名文件ID称为严格盲评；材料内容可能暴露模型或协作结构。未来需测试同源偏好、篇幅偏好、报告内prompt injection和独立会话一致性。
- 现有记录数与文件定位检查不等同完整沙箱验收。当前禁止生产模型运行；只读工具协议本身不是OS隔离证明。
- 按B，**本轮未给真实项目生成评分/模型评语**。合成测试外，只为已结束的旧Solo/Team准备离线V2证据包，未调用模型；见[RUBRIC_V2_PREPARATION_20260916.md](RUBRIC_V2_PREPARATION_20260916.md)。既有运行用冻结代码，不受新增模块影响。

## 未来真实接入的验收门（本轮未授权）

拟用Codex CLI+gpt-6-astra/xhigh/default，专用`/root/.codex-experiment`订阅、无API或当前账号回退、无子Agent和互联网。复用现有Host认证隔离与实时记录，但要为评审场景单独验收：实际身份、受限工具、禁止读取其他项目/账号配置、失败和超时、输出篡改、会话与成本完整性。每条项目独立会话，不延续其研究会话。不能仅凭配置名声称模型已验收。

原建议是每组一次、每会话30分钟、两次总会话且无自动重试；**用户选择B，故该额度未批准**。未来明确调用配额/本地CPU界限、重试与结束语义，再接生产transport。评审成本另记，不扣搜索CPU；不重新运行化学求解，也不回流当前搜索。未完成/无提交的项目未来可新增失败复盘协议，当前prepare明确拒绝，不能悄悄从性能比较分母删除失败运行。

Rubric是在看过开发结果后提出的，属探索性过程质量指标。正式论文应用前应以专家样例校准、记录分歧；若改权重/主张/主指标另行审查。不启用之前待审的60/40 grader。

## 本轮验收

`PYTHONPATH=src ../.venv-usc-official-compatible/bin/python -m pytest tests -q`：262通过、11跳过，30.64秒；其中32项Rubric测试。覆盖六维结构/英文提示、专用账号限制、NA/越界/伪引用、决策-评测双引用、实际读取绑定、路径与篡改、敏感字段清理、结束项目准备、原始评测缺失/被改拒绝、父参考导入、运行中/无endpoint拒绝、离线CLI、中文输出和V1兼容。测试输入全为合成数据；跳过项不算通过。没有执行真实模型或新科学求解。

## 官方参考链接

[OpenAI Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)建议清晰的评分准则、关注长度/顺序偏好并用人类标注检验评审器。这里将评分锚点、证据引用和人工复核分开实现，而不把单次强模型输出当客观真值。

[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)提供结构化输出及事件记录接口；这证明产品接口能力，不代表本模块已完成真实transport接入。
