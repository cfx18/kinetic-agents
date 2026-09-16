# 独立订阅账号：单／多 Agent 真实接口验收

2026-09-15 UTC。结论：**当前源码的 Codex 订阅接口可以交给用户自行提交任务。**
这里通过的是模型、工具、协作、恢复与现场记录的合成验收，不是机理优化效果或整套远端评测验收。
没有启动科研轨迹、SSH、Slurm、Cantera 求解或最终评测。

最新状态：下面记录的网页代理遗漏随后已正式修复到 v3 profile，公网与隔离回归通过。
见 [网页代理正式验收](WEB_PROXY_ACCEPTANCE_20260915.md)。本页真实模型调用仍是原 v2 时点证据；
v3 的补充验收不重复消费模型，不能把两批测试说成同一轮真实调用。

后续定位更新（同日）：模型接口验收结论不变，但网页代理链仍有一处正式配置遗漏。
对同一个开发者网页做原生沙箱临时对照，只改 `allow_upstream_proxy`，依次得到 false→403、true→200、false→403。
说明已有宿主代理可用，而正式网页工具未沿用该代理；并不是用户没有配置代理。
本次只是诊断，尚未把 true 应用到正式 profile，未做该变更后的隔离回归，不声称网页问题已正式修复。
原始阶段的“具体原因未确认”记录保留为历史，当前证据见
[代理路径对照](../local/acceptance/subscription-20260915T120217Z-9b3b89/proxy-route-ab.json)。
站点内部究竟依据地区、IP信誉还是其他规则拒绝，仍不知道。

## 1. 实际验收了什么

| 配置 | 实际模型 | 检查与结果 |
|---|---|---|
| 单 Agent | GPT-6 Astra / xhigh | 原生工具读取输入、运行 Python、写 JSON、调用宿主核验工具，在公开回复中引用真实返回凭条：通过 |
| 多 Agent 主控 | GPT-6 Astra / xhigh | 派出两个独立子会话；主控进程关闭后恢复同一会话，读取、审核、汇总子任务：通过 |
| 两个子 Agent | GPT-5.5 / high | 分别实际运行工具、写结果、提交版本化任务、返回凭条：均通过 |
| 现场记录 | 生产 LiveTranscript | 输入、公开输出、原生工具 I/O、宿主工具请求／返回、角色与恢复记录实时追加：通过 |

合成输入为 `[3, 5, 8, 13]`，正确结果为总和 29、平方和 267。主控最终文件包含两个子 Agent 的工具返回凭条；
不是只检查模型说了一句“成功”。单组未暴露子 Agent 创建工具。账号使用 `configs/common.yaml` 指定的
`/root/.codex-experiment`，没有回退桌面账号或 API。没有读取旧账号 ID，因此不声称两个目录必然是不同身份。

实际使用 `SubscriptionTeamClient`、`WorkerPool`、`TeamStore`、`ScientificTeamService`、`LiveTranscript`。
验收脚本的有限次调度替代了正式任务的后台执行所有者，不把它冒充完整 runner／调度器故障恢复测试。
也没有验证长上下文自动压缩、24 小时稳定性、远端镜像或集群当前可用性。

## 2. 本轮修复：宿主模型网络和 Agent 工具环境不能混为一层

第一次真实尝试停在模型连接阶段，出现 `responseStreamDisconnected`、`request timed out` 和重连通知，
没有观察到模型答复或工具执行。代码清理环境变量时，把宿主必需的 HTTP(S) 代理一起删掉了。

无凭据的同服务探针显示：直接连接约 12 秒超时；沿宿主代理约 1.19 秒收到预期的 HTTP 401。
401 仅证明服务可达，不证明登录成功；随后真实模型验收才补足这一层证据。

修复位于 `native/subscription.py`：可信的宿主认证／模型传输进程仅继承明确列出的代理和 CA 环境变量，
不继承 API key、任意启动脚本或其他身份设置。Agent shell 继续使用原有固定环境和权限配置；
代理值不进入身份快照或 transcript，只登记继承的变量名。参考了官方配置和 app-server 接口说明。

本机模拟 API 回归另显式设置测试用 `NO_PROXY=127.0.0.1,localhost`，避免把本机假服务送到外部代理。
这是测试进程设置，不改变生产 Agent 的网络权限，也没有启用模型／账号自动切换。

无模型的网络补查：

- 原生沙箱访问 `https://example.com` 和 `https://cantera.org` 均返回 200。
- `https://developers.openai.com/robots.txt` 在宿主返回 200、原生沙箱返回 Vercel 403。
- 因此并非所有公网访问失败；部分站点对不同网络路径返回不同结果，精确拒绝原因未确认。
- 没有为解决这个单站点 403 关闭沙箱、放开本地绑定或更改 `allow_upstream_proxy`。

启动终端须保留这台机器正常使用的代理配置；代码不会凭空创建代理。
网页站点拒绝仍可能影响某次资料获取，不能把两次 200 推广为“所有网站都可访问”。

## 3. 记录、失败和成本

成功目录：`local/acceptance/subscription-20260915T120217Z-9b3b89/`。

- [总验收结果](../local/acceptance/subscription-20260915T120217Z-9b3b89/acceptance.json)
- [单组现场 transcript](../local/acceptance/subscription-20260915T120217Z-9b3b89/solo/transcript.md)
- [多组现场 transcript](../local/acceptance/subscription-20260915T120217Z-9b3b89/team/transcript.md)
- [主控汇总文件](../local/acceptance/subscription-20260915T120217Z-9b3b89/team/work/TEAM_ACCEPTANCE.json)
- [无模型网络补查摘要](../local/acceptance/subscription-20260915T120217Z-9b3b89/network-check.json)

这些是本机私有验收记录，不随发布包分发；不含服务端隐藏 prompt 或私有思维链。
原生状态中的 `real_inference_verified: false` 是调用前的元数据检查标记；调用后的证明为上述 PASS 记录和实际 I/O，
不回写历史快照来制造“调用前已经通过”的假象。

成功尝试共 5 个原生 turn（单组 1、团队主控 2、子 Agent 各 1），**不是只有 5 次底层模型请求**。
单组验收耗时 51.262 秒，多组 158.092 秒；这些是合成接口耗时，不用于比较科研效率。
按每个角色最后保存的累计 usage 快照，totalTokens 分别为 54,324、50,335、85,434、85,227，合计 275,320。
输入缓存已经包含在相应输入统计中，不另加一次；这些是 CLI 报告的用量，不是美元账单或订阅剩余额度证明。

首次失败目录 `local/acceptance/subscription-20260915T115308Z-db9ae8/` 原样保留，另有 `interruption.json` 记录主动中断。
其原 `acceptance.json` 是中断前 RUNNING 快照，不代表仍在运行。该尝试启动过 1 个原生 turn，但未拿到 usage 回执，
所以不能把失败成本填成零。本轮总计启动过 6 个原生 turn；未新增 API 付费通道或购买额度。

常规回归：163 通过、9 个显式开启项跳过。实际 Codex CLI＋本机假 API 的两项单／混合模型回归另已通过。
当前修复在源码；此前构建的 0.2.0 压缩包未重建，不能用旧包代替这里验收的源码。

## 4. 用户启动：不需要再由聊天托管

本机现成 Python 环境可直接运行，无需重新安装：

```bash
cd /root/shared-nvme/Caifeixue/AgenticRL/kinetic_agents

# 单 Agent
../.venv-usc-official-compatible/bin/python run.py submit --config configs/solo.yaml

# 多 Agent；与上一条是两个独立任务，按需要分别执行
../.venv-usc-official-compatible/bin/python run.py submit --config configs/team.yaml
```

每次 `submit` 新建 `runs/时间戳…/`，打印实际运行目录并启动后台执行所有者。
两条都执行会分别占用两份配置预算，不是同一次任务的两个查看命令。
当前每次搜索 512 CPU 核时／24 小时、评测另计 64 核时；没有美元或模型调用次数硬上限。
仍使用独立登录目录的订阅额度，不能理解为无限用量或一定不会触发服务限额。

```bash
../.venv-usc-official-compatible/bin/python run.py status --run /提交命令返回的运行目录
../.venv-usc-official-compatible/bin/python run.py transcript --run /提交命令返回的运行目录
../.venv-usc-official-compatible/bin/python run.py results --run /提交命令返回的运行目录
```

关闭查看命令／聊天不会因此停止后台任务；关闭承载主机或主动杀掉后台进程则不是同一回事。
任务输入仍共享同一份 TASK 和父机理；这轮没有修改科学目标、搜索／评测隔离、评分规则或预算。
Claude 延期和 60/40 grader 待审状态不变。本轮没有新增论文科学主张。

若以后更换账号或驱动，需要主动重做真实接口检查时，可显式执行以下命令（会消耗所选订阅额度）：

```bash
../.venv-usc-official-compatible/bin/python tests/subscription_live_acceptance.py --run-real-models
```

本机本轮已经通过，不需要为了启动科研任务再重复验收。

官方接口依据：[app-server](https://learn.chatgpt.com/docs/app-server)、
[环境变量](https://learn.chatgpt.com/docs/config-file/environment-variables)、
[权限配置](https://learn.chatgpt.com/docs/permissions)。
