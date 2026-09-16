# 模型、连接、Harness 分开配置

三个可选的原生驱动：`codex`、`claude_code`、`kimi_code`。每个支持单 Agent 和主 Agent＋最多两名
子 Agent。底层推理、编码循环和上下文压缩交给各自 CLI；我们只管理生命周期、科研工具、预算与记录。
这不意味着任意模型都兼容，也不意味着三个底座已经通过真实科研质量评测。

## 选择与启动

| Harness | 单组配置 | 多组配置 | 模型连接 |
|---|---|---|---|
| Codex，默认订阅 | configs/solo.yaml | configs/team.yaml | 已登录的 Codex 账号 |
| Codex，自配 API | configs/codex-api-solo.yaml | configs/codex-api-team.yaml | Responses |
| Claude Code | configs/claude-solo.yaml | configs/claude-team.yaml | Anthropic Messages |
| Kimi Code | configs/kimi-solo.yaml | configs/kimi-team.yaml | Chat Completions / Responses / Anthropic |

统一命令 `python run.py submit --config configs/…yaml`。先用 `validate` 检查 YAML，
`doctor` 检查本机可执行程序、私有配置与有界网络连通性；这两条命令都不会调用模型/SSH/求解器。
`prepare` 在不启动计时的情况下冻结源码、非敏感配置、原生程序版本/文件哈希与 API 基础地址。
`.env` 不复制，也不哈希保存密钥；可轮换同一服务的 key，但改 endpoint、模型或 Harness 要新建运行。
`start --run …` 只运行原冻结配置。每次 submit 新建时间戳输出目录，不复制共同任务。

YAML 的 `harness.executable` 可填写命令名或完整路径，不是 shell 命令，不接受拼接参数。
Claude/Kimi 的主/子模型请把 `replace-with-…` 换成服务实际支持的 ID；prepare 会拒绝占位符。
Kimi `context_tokens` 要按所选模型填写；多组暂共用同一容量设置，必须不超过任一模型容量。
`reasoning_effort` 按底座语义指定：Codex minimal/low/medium/high/xhigh；
Claude low/medium/high/xhigh/max（服务/CLI 实际支持情况仍须验收）；Kimi thinking/off，不伪装成 xhigh。

## .env 和付费边界

项目已生成空 key 的 `.env`（0600），公开模板是 `.env.example`。只填写所选通道：
`CODEX_API_BASE_URL/CODEX_API_KEY`、`CLAUDE_API_BASE_URL/CLAUDE_API_KEY` 或 `KIMI_API_BASE_URL/KIMI_API_KEY`。
YAML 通过 `base_url_env`、`api_key_env` 引用字段名，也允许自定义字段名；不允许内嵌真实 key。
解析器只读取 KEY=value，不执行 shell、变量插值或命令替换；文件优先于宿主同名环境变量。
必须 HTTPS，只有本机模拟/本地服务允许 loopback HTTP；不跟随重定向，不自动重试上游请求。

真实 API key 仅供宿主 relay 使用。CLI 只得到当次 loopback token；relay 限制协议路由和登记模型，
转发前检查运行是否已结束。`native/api_requests.jsonl` 保存请求开始/完成/失败、模型和可获得的 usage，
不保存 HTTP 原文或 key。已接收的原生 token 通知仍放在兼容文件 `native/subscription_usage.json`，
其中 billing 在 API 模式标为 configured_API；文件旧前缀不代表用订阅付费。
未报告 usage 保留未知；流式 usage 可能是分段或累计快照，不直接全部相加当账单。

沿用用户选择：**当前不设美元上限或模型调用次数上限**。搜索全团队共享 512 核时/24h，
评测独立 64 核时；更换模型不会新增或重置账本。真正启动 API 模式会收费。
初版开发仅本机假 API 验收；2026-09-15 经用户授权另完成独立订阅账号的真实合成任务验收，
见 [订阅验收记录](SUBSCRIPTION_ACCEPTANCE_20260915.md)。没有据此自动启动科学实验。

公共 YAML 的 `network.env_file` 指向宿主私有 `.env.network`（0600），只读取 HTTP(S)/ALL/NO_PROXY 和 CA 字段；
私有文件完整替代终端同类变量，不执行 source、不记录代理值。Codex 订阅的认证/模型传输接收这一明确环境；
配置了 network 的 API 宿主 relay 也使用该路径，避免前检走代理、实际 API 请求却直连；旧无 network 绑定仍保持原路由。
API relay 目前只支持 HTTP/HTTPS 代理，SOCKS 在前检拒绝；API 的实际模型连通性本轮未重验。
Agent shell 仍用固定环境和原权限规则，未扩大私网或文件权限。
`doctor` 与 `start/submit` 做无凭据、有时限的连通性前检，403/超时/重定向等不会被当成通过；
前检不等于模型认证或完整网页/科学链验收。失败不启动模型/预算时钟；不保证运行中永不掉线。
本机不再要求用户从带 export 的终端启动；代理进程本身仍须可用。
同日进一步修复 Codex 订阅网页路径：solo/team 新 v3 profile 允许受控网络代理沿用宿主上游代理，
域名与私网过滤不关闭，开关写入冻结身份。真实网页与原生隔离检查通过，见
[网页代理验收](WEB_PROXY_ACCEPTANCE_20260915.md)。旧已准备运行不热替换；使用当前源码重新 submit。

## 接口与代码位置

- `connections.py`：凭据配置、协议兼容矩阵、endpoint 固定。
- `network.py`：宿主私有代理/CA 配置、清洁环境绑定、无凭据启动前检。
- `harnesses/catalog.py`：`HarnessSession` 生命周期接口、身份绑定、驱动工厂。
- `native/subscription.py` / `native/api.py`：Codex 订阅/API 两种传输，同一原生 app-server。
- `harnesses/stream.py`：Claude/Kimi print + stream-json；按 session ID 恢复，不靠全文重放。
- `harnesses/mcp.py`：按 actor 隔离的 MCP；调用原有 TeamRouter 和 ScopedScienceTools。
- `harnesses/gateway.py`：三类 HTTP API 的透明转发，**不做 Chat↔Responses 翻译**。
- `harnesses/sandbox.py`：复用本地 Codex 的 OS 沙箱命令；这里只用隔离原语，不调用它的模型循环。
- `observability/stream_review.py`：将现场公开记录送入既有中文 review 格式；MCP 以宿主记录为准，避免重复计数。

扩展新的 Harness：实现 `start_or_resume/begin/poll/close`，在 catalog 中注册构造器、协议与配置校验；
每次公开 I/O 调用 LiveTranscript，科学调用必须经 actor-bound 服务，不把回查数据或宿主 key 传给驱动。
`poll` 必须短时返回；不能把流式通知重新接到逐条数据库事务链。初始化失败必须关闭已创建资源。
未支持的 Harness 名称会拒绝，不能仅换命令字符串就宣称接通其他 SDK。

## 子 Agent 与隔离

三者都用 `research_spawn/research_followup/research_workers` 管理原生独立会话，模型由 YAML 显式指定。
各底座自带的不可核算递归 fork 被禁用；工人仍使用原生代码/工具循环，不是一次 LLM 摘要请求。
科学输入、工作目录、角色权限、总预算、最终提交规则不随 Harness 改变。
CLI 使用新 HOME/config/session；旧项目、评测包、.env 不挂载。Claude/Kimi 外层 OS 沙箱启动前做可读/不可写
任务、不可读宿主 canary 和原生 CLI --help 启动检查。任意检查失败时不开始预算时钟。

当前机器的 Claude 2.1.272/Bun 1.4.3 在外层沙箱中 --help 即 abort；它的真实 CLI 验收未通过，
因此暂不能启动正式任务。保留失败测试，不以配置检查替代真实运行；不能为通过测试扩大共享 /proc 可见性。
进一步诊断确认沙箱缺少自身 maps/fd；同版 musl 虽通过 --help，实际目录解析仍失败，未作为生产修复。
现在先检查这些运行能力，在模型/预算开始前返回明确原因；详见 [启动诊断](CLAUDE_SANDBOX_DIAGNOSIS_20260915.md)。
Codex/Kimi 的无付费真实 CLI 验收已通过。独立容器是后续候选方案，尚未实现或获验证。

限制：这不是容器级网络隔离审计。Claude/Kimi 需要访问模型/MCP loopback 端口，外层当前允许网络；
没有证明私网目的地或主动恶意 CLI 无法越权。安装目录只读挂载可能包含依赖，当前固定入口文件哈希与 CLI
版本，不等于所有依赖文件字节固定。由管理员选择的 CLI 是受信任执行依赖，不接受 Agent 自行替换。
若用于严格跨 Harness 隔离论文，应先补网络 allowlist/依赖封存及对抗验收；不把当前工程接口当该证明。

## 现场记录与证据边界

每次可捕获的输入、公开输出、原生事件、MCP 请求/返回，立即写 transcript.md/jsonl 和按 actor 分流记录。
隐藏 thinking/reasoning 内容在序列化前排除；公开决策依据通过 research_record_decision/query 保存。
CLI 没有暴露的内部消息或被截断内容不能恢复；Kimi stream-json 某些状态不输出 usage，所以另保留 API 宿主审计。
监控/聊天不是执行前提；原非 LLM 后台 supervisor 与独立评测服务继续使用。

当前验收和限制见 HARNESSES_ACCEPTANCE.md；0.1.0 的 ACCEPTANCE.md 是历史 Codex-only 结果，不能替新驱动背书。

接口依据：[Codex provider 配置](https://learn.chatgpt.com/docs/config-file/config-reference)、
[Claude Code 非交互运行](https://code.claude.com/docs/en/headless)、
[Claude MCP](https://code.claude.com/docs/en/mcp)、
[Kimi CLI 参数](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command)。
Kimi 实现另核对本机 1.50.0 的 config、agentspec 与 print 源码，以本机版本为准。
# Node Kimi Code 0.28.1 (2026-09-16)

`harness.name: kimi_code_node` is a distinct adapter for the installed
`@moonshot-ai/kimi-code` entry point. `kimi_code` remains the legacy Python CLI;
there is no automatic fallback. Expert profiles use the official platform's
`kimi-k3`, `max`, and the same expert-v2 task as Astra. Principal and researchers
use the same model. All provider requests are accounted, with no USD cap under
the user's same-as-Astra authorization.

The native CLI owns planning, compaction and sessions. Each host actor has its
own Kimi home and native session receipt. `Agent` and `AgentSwarm` remain visible
native tools but are denied by Kimi permissions; use the common, budgeted
`research_spawn` interface instead. Synthetic native tests deliberately attempt
an untracked child and verify its rejection, then exercise MCP, public I/O,
resumption and a scoped researcher. OS isolation is unchanged.

Version 0.28.1 rewrites persisted `thinking.effort=max` to `high` during its
one-time migration. The adapter instead declares the model's
`default_effort=max`. Native fake-API tests assert the actual outbound effort;
the production gateway rejects a request whose effort differs from the frozen
contract before contacting the supplier. No arbitrary max-output override is
injected; the native client's own output settings remain effective.

This is a model-plus-harness comparison against Astra, not a fixed-model harness
ablation. The initial API and science outcomes still require live observation.
