# Kimi Solo / Team：启动前准备

## 当前更新：用户已充值并恢复启动授权

2026-09-16用户明确“可以挂上了”，恢复同Astra资源：每组512搜索核时/24h及64独立评测核时，API无美元帽。旧取消记录保留于下，不再是当前启动阻塞。

现已接入既有Node Kimi Code0.28.1，驱动名kimi_code_node，配置kimi-k3/max。未修改邻项目、全局CLI或凭据。与Astra共用同一个专家V2任务；Solo不建科研子Agent，Team最多两名同模型研究者。固定模型请求由宿主网关校验，原生Agent/AgentSwarm可见但调用被拒绝，团队走受控research_spawn。

首轮发现--prompt/--auto冲突及客户端首次迁移将thinking.effort=max改high，均在本机模拟服务发现，无供应商消费。现用model.default_effort=max，并在网关要求实际请求max。原生新旧Kimi Solo/Team本机测试4通过/2未选，38.52秒；含工具执行、拒绝不受控子Agent、原会话恢复、受控子Agent、实时公开IO。全套第一次269通过13跳过；最新追加错误路径测试后待复跑。无凭据网络前检返回404，证明可达，不代表已通过真实账号推理。

下一步冻结两份独立运行、同输入哈希、统一资源与身份；通过最终回归后启动。首次供应商请求和科研搜索计入各自账户，不额外购买、不重置Astra，不调用Rubric。

## 历史记录（被上述最新状态覆盖）

状态：**用户因费用撤回，实验取消且未启动**。配置保留为草案；已装旧客户端的本机模拟API验收不代表新版Node已接入。真实Kimi模型调用与本次新远端任务均未启动。此前同Astra限额授权已撤回，后续不得据此自动启动。用户其他项目的Kimi账单和作业不在本次取消范围。

## 用户指定位置后的更正

已找到`/root/shared-nvme/Caifeixue/AgentCFD/AgentCFD_Terminal_Bench/.env.kimi-official`，字段`SCIENCE_API_BASE`/`SCIENCE_API_KEY`。Key非空、文件权限符合本项目要求，地址为`https://api.moonshot.cn/v1`，即国内官方按量API，不是Code套餐。两份新配置已直接引用该文件和变量名，未复制密钥，也无需用户重填本项目.env。此前只检查了本项目.env，不代表机器没有Kimi凭据。

同目录还已有私有Node `@moonshot-ai/kimi-code 0.28.1`，`--version/--help`成功。位置为`.harness-runtime/kimi-code-0.28.1/node_modules/@moonshot-ai/kimi-code/dist/main.mjs`。此前只查看PATH导致遗漏；不需要重新安装。该新版仍须接本项目的科研MCP、主子Agent生命周期、隔离和记录，不能直接套Python桥接或借用另一个benchmark的科学结果作为本项目验收。

两份配置现已解决凭据绑定，其他部分仍是明确标注的待启动草案。尚未切换成已验收Node驱动，不能据Key存在就启动长程任务。剩余门：新预算与现代客户端接入/真实API验收。已询问的服务类型问题被上述实查解决，不需用户再次回答。

## 实验范围

两组共用当前Astra专家提示版`tasks/usc_ii_expert_v2/TASK.md`及同一父机理、求解工具和独立610历史暴露评测。不是改写任务或沿用旧候选。每次启动生成独立时间戳目录，实时transcript、决策记录、资源账本、最终提交与评测分别归档。原Astra运行不改、不停止、不分享搜索证据。

- `configs/expert-v2-kimi-solo.yaml`：主Agent一名，不允许子Agent。
- `configs/expert-v2-kimi-team.yaml`：同一主模型，最多两个同模型科研子Agent，共享组内预算。
- 暂拟开放平台`kimi-k3`，上下文1048576；用户购买渠道与可用模型确定后才能锁定真实运行。Code套餐使用不同的模型ID，不自动替换。
- 与Astra的比较是模型+Harness整体系统比较，不是固定同模型的Harness因果实验。Kimi Solo/Team两组拟用相同主/子模型，控制模型种类，但单次重复不足以作强统计结论。

## `.env`怎么填

通用模板仍是`kinetic_agents/.env`；这次实际采用上方用户指定的外部.env文件。初查本项目`KIMI_API_KEY`和`KIMI_API_BASE_URL`为空的记录保留，不再作为当前阻塞。没有打印凭据，没有从全局Kimi账号文件取Key，也没有覆盖现有文件。

只在文件里填这两个字段，权限保持600，不要把Key贴聊天或写YAML：

```dotenv
KIMI_API_BASE_URL=<the URL matching your purchased service>
KIMI_API_KEY=<fill locally>
```

服务必须匹配：

| 购买渠道 | Base URL | 计费/身份 |
|---|---|---|
| 开放平台国际区 | `https://api.moonshot.ai/v1` | 按token付费，模型如`kimi-k3` |
| 开放平台中国区 | `https://api.moonshot.cn/v1` | 按token付费，使用对应平台Key |
| Kimi Code套餐 | `https://api.kimi.com/coding/v1` | 套餐权益、不同模型ID与可用档位，不能按开放平台价格臆算 |

`.env.example`已补英文说明。后台真实Key仅由宿主网关持有；Agent接触运行内临时令牌，不复制上游Key到任务、产出或transcript。

## 客户端版本不能混淆

本机`kimi`实际指向`/root/.local/share/uv/tools/kimi-cli/bin/kimi`，Python发行包Kimi CLI **1.50.0**。当前桥接、参数与验收针对该版本。新配置显式绑定这个路径，避免PATH改变后意外运行另一种CLI。

官方当前Kimi Code CLI已迁移Node，新版参数/配置不同，不能套用旧`--print/--config-file/--agent-file`接口或宣称旧验收覆盖新版。用户若要新版官方Node产品，应独立部署与适配，不覆盖全局客户端或假借旧版运行结果。

现有`reasoning_effort: thinking`经Python客户端映射为API的`high`，**不是K3 max，更不是Astra xhigh**。尚未进行真实K3请求验收；静态可用不等于服务端已接受。

参考：[官方CLI迁移说明](https://www.kimi.com/code/docs/en/kimi-code-cli/guides/migration.html)、[平台模型列表](https://platform.kimi.ai/docs/models)、[K3 reasoning effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[Code与开放平台区别](https://www.kimi.com/code/docs/en/)。

## 验收与未完成项

- 本地适配测试：20通过、1跳过，0.83秒。包括两份新配置与Astra相同任务/资源、不同输出前缀、Solo/Team成员数。
- 真实已安装Kimi CLI→本机模拟API→科研MCP：2通过、2个不相关Codex测试未选，25.76秒。覆盖单组、多组子Agent、工具IO、隔离与会话恢复。没有供应商请求、真实Key或新科学求解。
- 初次默认沙箱禁止本机socket创建而失败；在获准本机网络权限后通过，未放宽受评Agent文件沙箱。
- `doctor`在空Base URL处拒绝，未进入模型或科学启动；这不是供应商拒绝或Key无效的证据。
- 原生API网关目前仅记录请求/用量，并没有美元硬上限。不能将订阅模式的无美元帽直接套给按量API。若批准金额上限，必须先接入全团队请求预留/结算与未知失败占额，并通过验收；不能只填一个配置数字。

## 待用户选择

1. 渠道与Key位置已确认：国内官方开放平台API，无需重新询问。
2. 新资源上限：拟沿用每组512搜索核时/24小时+64独立评测核时，总1152核时；不是重置Astra旧账本。
3. 按量API备选：A每组$25/共$50（推荐有限预算首轮），B每组$50/共$100（探索余量更大），C先配不跑（零供应商消费）。两档均含主/子模型、重试与收尾。达到金额上限可能提前停止，不能描述成与无美元帽Astra等资源。套餐则明确允许消耗的权益范围，不能虚构美元上限。

金额、实际渠道、Key和客户端选择未落实前，不启动长程实验。没有发送邮件；SMTP未配置（需要`MECHRL_REVIEW_SMTP_FROM`及`MECHRL_REVIEW_SMTP_AUTH_CODE`，仅配置机器环境）。
