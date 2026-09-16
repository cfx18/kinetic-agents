# 独立启动网络修复与旧运行取消（2026-09-15）

## 结果

用户要求保留 `default`、取消两条正在重连的脚本、修好后由用户重新提交。
已通过运行器的 stop 接口取消，未按进程名批量杀进程、未取消其他人的远端任务。

| 旧运行 | 终态 | 本地进程树 | 实际本地 CPU 秒 | 远端任务 | 科学提交 |
|---|---|---|---:|---:|---|
| solo-20260915T124435.326800Z-b5e7bb94 | CANCELLED | SETTLED / stop_requested | 7.356178 | 0 | 无 |
| team-20260915T124436.673430Z-e2bbcac4 | CANCELLED | SETTLED / stop_requested | 7.034823 | 0 | 无 |

宿主复查原 supervisor PID 1090603 / 1090840 已退出；所有者完成自身后代清理后结算。
旧 transcript、result、源码快照、账本和截止时间保留，不删除、不清零、不覆盖成成功实验。
没有有效 token usage，不把未知订阅消耗写成 0；本轮修复验证未调用真实模型。

## 原因和改动

旧版本只把启动终端**已经存在**的代理环境传给认证/模型进程。
验收终端有代理，用户独立终端没有，所以两条任务活着但一直在网络层重连。
这是部署配置依赖终端、且缺启动前检的工程问题，不是科研策略差或求解器慢。

现在：

1. `configs/common.yaml` 增加 `network`，指向私有 `.env.network`；本机已填写可用宿主路线，权限0600。
   公开模板 `network.env.example` 只有示例值。不要复制本机私有文件到公开代码。
2. 启动器直接解析 dotenv，不执行 source；私有文件完整替代继承的 proxy/CA 环境，大小写别名一致。
   缺文件、权限不对、缺必需代理、未知字段、命令替换等都会拒绝。
3. `doctor` / `start` 在启动模型和预算时钟**之前**做一次无凭据、有时限的 HTTPS 探测。
   不读认证令牌、不发送 prompt、不调用推理、不跟随重定向；403/407/超时/限流/服务错误均不通过。
   强制代理时，不能用 `NO_PROXY=*` 悄悄绕开该要求。
4. 通过后，同一份已解析环境传给脱离终端的 supervisor，再传给可信认证/模型进程。
   代理值不写入快照、transcript 或 Agent shell；shell 与网络过滤权限未放宽。
5. `network_preflight.json` 留下状态、耗时和变量名，status 可读取。
   API relay 的新 network 绑定也显式接代理/CA，避免前检走代理、实际请求却直连；旧无 network 契约保持旧行为。
   API relay 不支持 SOCKS，配置后会前置拒绝，不静默换协议；本轮不宣称真实 API/其他 Harness 已重验。

网络与沙箱权限仍按各自边界控制，参考 [OpenAI Docs：Permissions](https://learn.chatgpt.com/docs/permissions)。

## 验证与证据边界

- 常规回归：197 passed / 11 skipped。
- 实际 Codex CLI＋本机假模型／合成代理：7 passed，覆盖单组、团队工具/恢复以及网页隔离。
- **完全清空继承环境**（env -i，仅保留 PATH/LANG）后，solo/team 的 doctor 均退出0：
  官方模型服务无凭据请求返回401，耗时分别0.967秒、1.849秒。
  401在这里表示请求到达需要认证的服务，**不表示账号认证成功或模型已生成答案**。
- 测试确认网络失败时没有 Popen、runtime.sqlite 或 launch_intent；成功时读取的路由传给后台进程。
- 任务文本、父机理、模型/effort、default服务档位、资源设置、评分和回查边界不变。

无新真实模型调用、SSH/Slurm或科学求解；没有新的机理效果结论。
预检不是全天网络稳定性保证，不能排除后续代理服务退出、认证失效或上游限流。
本机代理进程仍需保持运行；对话不需要保持打开。

## 用户重新提交

不要对已取消的旧目录执行 start，也不要删除其执行所有者记录。
从当前源码重新 submit，生成新的带时间戳目录；旧失败记录继续保留。

```bash
cd /root/shared-nvme/Caifeixue/AgenticRL/kinetic_agents
../.venv-usc-official-compatible/bin/python run.py submit --config configs/solo.yaml
../.venv-usc-official-compatible/bin/python run.py submit --config configs/team.yaml
```

无需 source/export。每条 submit 自带联网前检；也可先独立运行 `doctor --config configs/solo.yaml`。
本轮没有代替用户执行这些提交命令，不热补丁旧冻结代码；旧0.2.0安装包也不会自动更新。

## 大图景／下一步

当前是成熟 Harness 强基线的可独立运行部署阶段，尚未新增算法或PDE科学证据。
本轮价值是消除“验收能联网、独立启动不能”的环境差异，并让启动故障在科研前显式失败。
无新增科学审查事项；既有 grader 等独立审查不变。下一步由用户重新挂任务，采集真实优化轨迹。
