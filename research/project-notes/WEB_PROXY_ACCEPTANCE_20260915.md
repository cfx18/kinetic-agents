# Codex 订阅网页代理：正式修复与验收

2026-09-15。**当前源码已修复单／多 Agent 的网页代理接线，用户可以继续自行提交新任务。**
模型、账号、科研输入、预算、评分均未改；本轮没有真实模型调用、SSH、Slurm 或科研求解。

## 原因和修复

此前宿主模型传输已经继承代理，但网页工具仍用 `allow_upstream_proxy=false`。
同一网页在临时沙箱中切换 false/true/false，得到 403/200/403，证实两条网络路径没有同时接通。

现在 `native/profiles.py` 中的单／多 Agent 使用 v3 profile，把 `allow_upstream_proxy=true` 写入配置身份快照。
请求路径是 **Agent 工具 → Codex 受控网络代理 → 宿主既有代理 → 公网**；不是让工具直接继承宿主环境。
保留网络过滤、`allow_local_binding=false`、固定 shell 环境、任务只读与工作目录之外的文件拒绝。
旧 controlled/open-world 类未改，旧运行的 source-release、任务契约和结果未修改。
已经 prepare 的旧运行不会自动升级；要用本次修复应重新 submit，从当前源码新建运行。

设置依据：[OpenAI 官方权限文档](https://learn.chatgpt.com/docs/permissions)。该开关用于上游代理路由，
不是关闭网络过滤或私网保护。本次修复特指现有 Codex 订阅链，不代表其他 Harness 的全部网络环境也已验收。

## 实测

| 检查 | 结果 |
|---|---|
| 正式 solo-v3 访问 OpenAI 开发者 robots 页面 | 200 |
| 正式 team-fork-v3 访问同一页面 | 200 |
| Cantera 官网 | team 首次 200；solo 首次 URLError，限定一次复查为 200 |
| 单／多 Agent 原生 CLI＋本机假模型 | 工具、现场 transcript、子任务和会话恢复回归通过 |
| 合成上游代理 | 允许的请求收到真实测试响应；被拒请求没有到达该代理 |
| 拒绝的请求 | localhost、127.0.0.1、10.0.0.1、云元数据地址、显式禁用域名均 403；IPv6 回环 URL 被原生解析层以 400 拒绝 |
| 文件／直连检查 | 宿主私有 canary、符号链接逃逸、任务写入和直连宿主代理端口均失败；工作目录写入正常 |

常规回归 **166 通过、11 默认跳过**；另显式开启的定向测试 **7 通过**（含 3 个配置测试、
2 个真实 CLI 假模型测试、2 个原生网络测试，不应与常规数量直接相加）。
新测试位于 `tests/test_native_web_proxy.py`，默认不会启动原生进程或外网检查。
合成代理仅返回固定响应，不访问 URL 中的外部 HTTP 目标；实际 HTTPS 网站检查单独进行。

保留的负面证据：初版合成夹具使用 example.com，组合回归出现一次 403，随后单独重跑通过，
当时没有完整拒绝原因，不能定案为 DNS 故障。最终夹具改用公共 IP 字面量去掉不必要的 DNS 依赖；
公网 DNS／HTTPS 的能力仍由上方真实网站检查体现，不能拿合成代理结果替代。
IPv6 的 400 是拒绝证据，不是 IPv6 URL 支持或所有 IPv6 防护路径的证明。

完整摘要：[本机验收记录](../local/acceptance/web-proxy-v3-20260915/acceptance.json)。
之前真实 Astra/GPT-5.5 模型验收是在 v2 profile 下完成，本轮 v3 使用真实 CLI＋假模型做回归，
没有为修网页重复消费订阅额度；两批证据的范围不能混写。

## 边界与下一步

目前处于成熟 Harness 的可靠底座阶段，不产生新的机理优化、PDE、RSI 或论文主张。
修复的是已确认的代理接线，不能保证所有站点永久返回 200；代理服务本身必须在启动终端中配置并可用。
本轮不是全协议攻击测试、24 小时稳定性证明或远端计算／独立评测验收。
旧 0.2.0 构建包没有重建，使用当前源码入口；已冻结的旧运行不热替换代码。

无需再次登录或改变预算。用户按需要分别执行，两个命令会创建两个独立任务：

```bash
cd /root/shared-nvme/Caifeixue/AgenticRL/kinetic_agents
../.venv-usc-official-compatible/bin/python run.py submit --config configs/solo.yaml
../.venv-usc-official-compatible/bin/python run.py submit --config configs/team.yaml
```

每次产生新的时间戳目录、配置／源码快照和实时 transcript；查看命令不是任务推进前提。
本次修复没有自动执行上述 submit。Claude 延期、60/40 grader 待审、现有资源和评测边界保持原状。
