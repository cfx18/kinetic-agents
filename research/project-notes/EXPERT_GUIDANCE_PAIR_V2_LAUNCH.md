# 专家提示增强 Solo / Team V2：启动记录

用户批准：2026-09-16对话“确认你的建议”，选择B及原上限。运行目录时间由宿主UTC时钟生成，本次显示2026-09-15；不将聊天日期冒充运行时间，不手改计费时钟。

## 本轮改变与不变

新共同输入：`tasks/usc_ii_expert_v2/TASK.md`，原任务正文不变，仅追加已批准草案中B的英文内容。SHA-256：`dee27db8a8c1332d8e704fc55697eb52eee8d88acac4bc9c4ca9acd33284a2a5`。同一原父机理111物种/784反应，哈希与V1一致。

追加FFCM-2/SFCPD、ReSpecTh、ChemKED线索，覆盖解释、测量定义与不确定度、实验/父参考区分、方法选择与自写工具核验。不给旧机理/排名/图接口修复/物种恢复答案。旧任务与已结束运行保持原样。

配置：`configs/expert-v2-common.yaml`统一输入和资源，`expert-v2-solo.yaml`与`expert-v2-team.yaml`只控制系统组合。任务注册支持两个明确批准哈希，每次契约/manifest/科研状态仍绑定唯一任务，不能在恢复时换成另一个已批准任务。其余科学/模型策略不改。

## 两条新运行

| 项目 | Solo | Team |
|---|---|---|
| 运行目录 | `runs/expert-v2-solo-20260915T173616.942323Z-0bbd299d` | `runs/expert-v2-team-20260915T173617.847670Z-2c1bfc9d` |
| 运行ID | `cfx_astra_eaab23cebb934bb2ba9f69f0` | `cfx_astra_61bc9612470e4d46ab08ef0e` |
| 主模型 | Astra/xhigh/default | Astra/xhigh/default |
| 子Agent | 禁用 | 最多2个GPT-5.5/high，自主决定分工 |
| 所有者PID（启动时） | 1270788 | 1272046 |
| 搜索上限 | 512分配核时/24小时 | 512分配核时/24小时 |
| 独立终点评测 | 64核时 | 64核时 |

两组总1152核时上限，搜索/评测分账；专用订阅，不回退API、不自动购买。窗口不是64核连续占满24小时。远端只经sca2070/sbatch，cfx作业名，N1/n64，文件在`/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/kinetic-agents`的独立run目录。启动所有者不意味着已提交远端科学作业，以job记录为准。

## 验收与当前证据

- `PYTHONPATH=src ../.venv-usc-official-compatible/bin/python -m pytest tests -q`：230通过，11跳过，30.01秒。包括新增任务同源/批准文本精确追加、V1兼容、双模式V2准备/加载与任务替换拒绝回归。跳过测试不记为通过；复用既有原生合成验收，不另做付费合成科研。
- 宿主HTTPS无凭据探测通过，401只证明连通性；首次沙箱内curl7、首次权限复核容量失败均发生于模型/预算启动前。按明确无凭据范围重新获准检查后通过，没有绕过权限拒绝。
- 两组正式启动后`status=RUNNING`、`failures=0`；原生身份记录均观测到Astra/xhigh，实时transcript已有模型公开输出。Team子Agent是否实际创建以workers/usage记录为准，不把允许创建当实际创建。
- 每组后台所有者独立推进与收尾，`observer_required=false`；旁路状态查询不是执行前提。所有可捕获公开模型/工具IO实时写入各组`transcript.md/jsonl`，不声称完整隐藏思维链。

## 看结果与记录

运行目录下的`config.resolved.yaml`和`preflight.json`保存冻结配置/任务/代码身份；`solo-max`或`team-max`内看`transcript.md`、`work/`、`final_artifacts/`、`endpoint/scorecard.json`。结果文件未产生时不代表失败；需结合status判断。

```bash
../.venv-usc-official-compatible/bin/python run.py status --run runs/expert-v2-solo-20260915T173616.942323Z-0bbd299d
../.venv-usc-official-compatible/bin/python run.py status --run runs/expert-v2-team-20260915T173617.847670Z-2c1bfc9d
```

本轮已启动，不要重复submit创建新实验；观察可将status换成transcript或results。

## 科学边界与下一步

当前只证明工程接入与实际模型启动，尚无新机理或精度收益。观察专业线索是否进入检索、案例组织、方法核验和停止决策；不进行中途人工科学指导。锁定提交后独立运行同610历史暴露工况，已修复gas/bulk入口，沿用原误差/覆盖/复杂度，不启用待审60/40 grader，不向已结束搜索反馈评分。单对新轨迹和历史比较不足以证明提示单条贡献、架构因果或RSI；数据库与历史池可能重叠，不称全新盲测。
