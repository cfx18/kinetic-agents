# 0.2.0 可配置 Harness：验收与未通过项

日期：2026-09-15。仅本机合成 API/数据；真实推理请求、SSH、Slurm 和科研求解均为 0。
原有 0.1.0 的验收文档保留，不用于替新驱动背书。

后续启动诊断/保护修复见 [CLAUDE_SANDBOX_DIAGNOSIS_20260915.md](CLAUDE_SANDBOX_DIAGNOSIS_20260915.md)：
161 常规测试通过，启用原生 Codex/Kimi 后 168 项通过；Claude 仍因自身 procfs 缺失阻塞。
下方 0.2.0 数值与分发包作为历史验收保留。

| 组合 | 实际 CLI | 已验证 | 当前结论 |
|---|---|---|---|
| Codex 订阅传输 | 0.153.4＋本机假 Responses | 原单/多组科研工具、提交锁定、实时捕获、恢复 | 原回归通过；不是新账号/真实推理验收 |
| Codex 自配 API | 0.153.4＋本机假 Responses | 主模型工具调用、不同子模型独立会话/工具/角色/用量 | 本地接口通过 |
| Kimi Code 自配 API | 1.50.0＋本机假 Chat Completions | 隔离、主/子 Agent、MCP、API relay、现场记录、原会话恢复、中文 review | 本地接口通过；其他 Kimi API 协议需逐服务验收 |
| Claude Code 自配 API | 2.1.272，Bun 1.4.3 | 配置/命令/协议/MCP单测；实际 --help 进入外层沙箱即 abort | **未通过，不可正式启动** |

最后回归：常规测试 **158 passed，9 skipped，23.24 秒**；明确启用本机原生 CLI 验收、
排除独立 Claude 原生测试文件后为 **165 passed，70.03 秒**。
两个 Claude 原生测试保留，不改成“假通过”；配置与 mock 测试不能代替它们。
追加复核 `-x tests/test_claude_native_pipeline.py`：**1 failed，2.22 秒**，在单组 bootstrap
报 `PermissionError: native CLI cannot bootstrap inside isolation; no budget clocks or model requests started`，
多组因 `-x` 未继续执行，不能宣称两项均已独立验收。

## 验收发现与修复

- 原实现多处硬编码 Astra/xhigh、GPT-5.5；现由冻结契约传递到主/子会话，订阅按实际模型目录检查，不静默替换。
- API-only Codex 也发送 account/updated 通知。现在忽略非请求通知，仍拒绝要求 OAuth/订阅认证的请求。
- Codex 测试工具必须统一 canonical schema；修正合成测试缺失 type 的定义，没有放宽生产校验。
- Kimi 的纯文字 content 可以是字符串而非文本块数组；实时 worker 回复与复盘都处理两种形式。
- 外层 CLI 需要专用 CODEX_HOME 和临时目录。初始化目录并保留原生状态在每 actor 私有目录。
- 用量未知不写成零美元：API relay 保存开始/完成/失败和可获得 usage；订阅指标仍不冒充账单。
- `.env` 不 source，不随源码/配置快照复制；endpoint 固定，key 可轮换。API 与订阅不能自动切换。

## Claude 失败的证据边界

宿主直接 `claude --version/--help` 正常；同一二进制放入当前外层沙箱，`--help` 也在约 3ms 内
返回 134，输出 `panic(main thread): abort() called`。这是启动兼容失败，不是模型质量或额度问题。
专用 TMPDIR、关闭 JIT 的诊断及精确系统信息只读文件试验未修复。后两项未保留为生产配置。
只读 syscall 诊断发现多项 proc/sysfs 读取差异；尚未证明它们就是根因，不能声称已定位到某一个文件。
尝试设计更宽 proc 视图的进一步诊断被平台自动审查拒绝：可能暴露其他进程信息；该调用没有执行，未绕过。
生产仍保持原严格文件边界；start 在预算计时/模型连接之前运行 CLI bootstrap 检查，失败即停止。
下一候选是隔离容器部署或修正外层沙箱兼容性；需要另行推进，不能把它写成已经支持正式运行。

调试 strace 只下载、解包至专用 /tmp 目录，没有安装系统包、没有修改 Claude 或用户账号。
诊断脚本未进入公开包，失败事实保存在本报告和原生验收测试中。

## 仍不能宣称

- 不能保证任意第三方 model ID、推理档位、Responses/Anthropic 实现均可用。
- 不能把这些合成工具验收当成真实机理优化、RSI、跨 Harness 公平消融或长程稳定性结果。
- Claude/Kimi 外层目前不是完整网络 allowlist 隔离审计；正式论文的隔离证明仍需额外工作。
- 当前不设美元上限。切 API 后的真实消费由服务商计费；本轮没有启动该消费。
- 任务正文、父机理和固定评分源保持不变，待审的 60/40 grader 未启用。

## 重现本轮检查

在项目目录、Python 3.12 环境中执行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q --tb=short
RUN_NATIVE_TEAM_QUALIFICATION=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -B -m pytest -q --tb=short --ignore=tests/test_claude_native_pipeline.py
# 当前已知失败项，单独执行会如实返回失败，不属于上一条的通过计数：
RUN_NATIVE_TEAM_QUALIFICATION=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -B -m pytest -q --tb=short -x tests/test_claude_native_pipeline.py
```

原生测试需要允许本机监听和嵌套 OS 沙箱；仅连接合成服务，没有账号登录或真实模型计费。
`validate` 已对全部 8 个单/多 YAML 配置通过；所有组合引用相同任务/资源。
未在真实供应商验证模型 ID、套餐、价格或科研质量。

## 本地分发包检查

已生成 0.2.0 wheel（97 个条目）与 sdist（171 个条目），保留旧 0.1.0 包不覆盖。
实际 .env、parent.yaml、local/、runs/、auth.json、runtime.sqlite 均未入包；
sdist 包含 .env.example 及本轮验收记录，wheel 包含驱动代码。未上传或外发。
wheel 离线安装到专用 /tmp 目录后，项目外导入 89 个模块、构建/复核 91 文件自身冻结快照通过。
科学进程的 sitecustomize 需要已有的 CFX_SCIENCE_LOG_DIR；首次通用遍历未设置时按设计拒绝，
按该模块的运行契约设置专用临时日志目录后全部导入通过，没有执行科学求解。

本机科学虚拟环境没有 setuptools，全局 packaging 23.2 又不满足构建要求；
最终采用现有 setuptools 78.1.0＋科学环境 packaging 26.3 构建，没有安装/升级系统依赖。
