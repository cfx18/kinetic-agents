# 分层与数据归属

| 层 | 代码位置 | 责任 | 不负责 |
|---|---|---|---|
| 契约与入口 | `config.py`, `cli.py`, `runner.py` | 共享输入、配置快照、启动与收尾 | 改写研究目标 |
| 原生 Harness 适配 | `harnesses/`, `native/` | Codex app-server、Claude/Kimi stream-json、角色、子会话、快消费通知 | 自己实现模型推理与上下文压缩 |
| 模型连接 | `connections.py`, `harnesses/gateway.py` | 主机 .env、协议校验、只转发已登记模型、调用审计 | 默认收费授权、隐式协议翻译或自动换模型 |
| 科研状态 | `team/` | 任务、问题、证据、memory/skill 版本、启用/撤销 | 将总结自动升级为科学结论 |
| 研究接口 | `research/` | 工具授权、分工协议、决策／查询记录 | 给定优化算法或候选池 |
| 运行控制 | `core/`, `execution/` | 生命周期、CPU、独立目录、Slurm、异常结算 | 由聊天监控推进运行 |
| 可观测性 | `observability/` | 实时 transcript、中文 review、结构化报告 | 重建不可见思维链 |
| 独立评分 | `evaluation/` | 锁定提交后计算固定池、独立账本 | 把评分回流同一轮优化 |

## 数据结构

公共输入只有 `tasks/usc_ii/TASK.md` 和 `parent.yaml`。新运行中的
`input-reference.json` 指向它，preflight 同时锁定引用、输入字节及配置。
Native 权限策略只把这一个公共目录设为只读，把本轮 `work/` 设为可写；
不会挂载项目根目录、其他运行、评分数据或账号目录。

每轮数据分开保存：

1. `runtime.sqlite`：生命周期、期限、资源核算、工具轨迹。
2. `team/team.sqlite`：带 actor、版本、证据引用的任务／问题／笔记／memory／skill。
3. `work/` 与证据归档：Agent 自建文件、原始实算输入输出；结构化状态引用它们。
4. `transcript.jsonl`：可捕获模型与工具 I/O，按捕获顺序立即落盘，另有 Markdown 视图。
5. `native/`：原生会话状态；不把完整历史重复灌进下一轮 prompt。

memory 和 skill 是可提出、检查、启用、使用及撤销的版本化对象；原始证据保留，
查询与程序记忆不是同一种数据。后续算法改进应围绕这些接口做版本化策略实验，
不能把这次文件整理声称为 RSI 效果。

## 执行链

`YAML → prepare → start → CPU owner → native principal/workers → locked submission
→ remote settlement → independent evaluator → review/results`

`status/transcript/results` 是旁路。普通流式通知不触发逐条预算数据库重写。
收费调用、科学执行提交和关键状态变更仍做授权与持久化。

远端只使用 `ssh sca2070` 提交带 `cfx` 名称的 `sbatch -N 1 -n 64`。
新任务使用自己的路径，镜像和评测 Python 是受控部署资源，不是旧实验结果。
镜像/站点挂载发生变化须重做隔离验收，不可绕过校验。

## 保持科学可比性

`evaluation/_frozen/mechrl/` 的数值文件保留原字节。原 benchmark 还绑定一个历史
优化器源文件的校验值；它只保留为 `_frozen/pins/usc_three_mode.py.txt`，不导入、不运行。
其余无关旧优化器、付费 DeepSeek 网关、恢复专项脚本、历史结果都不迁入。
这样不必为了改包名悄悄替换科学源身份。
