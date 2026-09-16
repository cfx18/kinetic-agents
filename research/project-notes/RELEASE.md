# 公开候选包，不是授权发布

当前操作只创建本地项目，不上传 GitHub、不发送数据、不启用新论文主张。
0.2.0 增加可配置模型/API 以及 Claude/Kimi 驱动；Claude 启动兼容性未通过，不能标为稳定支持。
本轮验收见 `HARNESSES_ACCEPTANCE.md`，原 `ACCEPTANCE.md` 只记录 0.1.0 历史验收。
MIT 适用于项目原创代码；Codex、Claude Code、Kimi Code、Cantera、pyMARS 等作为外部依赖安装，不复制其源码。
冻结目录是本项目既有科学适配代码，不是第三方软件的重新授权。

明确排除：`local/` 私有内容、`runs/`、`parent.yaml`、凭据、环境目录、旧研究仓库、
Git 历史、模型会话、实验观测和未审核论文数据。不要直接 ZIP 整个本地目录发布。
仅 `.env.example` 空凭据模板进入源包，实际 `.env` 永不打包。
分发 Python wheel 只包含 src 包；源材料使用明确的打包清单。

公开前仍须核对：数据再分发权限、作者与引用、目标机器的原生 Codex/Slurm 隔离验收，
以及论文 claim 对应证据。通过无费用工程测试不等于获得新的科学结果。

旧项目及旧 prepared/source-release 不做就地替换：它们是历史证据。
新项目运行不 import 旧仓库。迁移对应关系见 `migration-map.json`。
