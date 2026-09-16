# Claude 启动问题：原因、已修项、剩余环境要求

结论：不是 PATH、API key 或模型额度的问题。当前外层沙箱没有可用的自身进程元数据，
与 Claude 2.1.272/Bun 1.4.3 不兼容。**当前主机上的完整 Claude 接口仍未验收通过。**
本轮没有模型计费、账号读取、Slurm 或科研求解；未修改系统 Claude 或系统运行库。

## 已确认的因果链

1. 清洁环境下原 glibc Claude `--help` 正常，退出 0；同一个程序在严格沙箱中退出 134。
2. 跟踪本次新建进程看到 `mount("proc", "/newroot/proc", ...)=EPERM`；
   沙箱随后启动了不含该 procfs 的进程视图。没有放开宿主 /proc 进行对照。
3. 沙箱中 `open("/proc/self/maps")=ENOENT`。最小系统库探针的 `pthread_getattr_np`
   在普通环境返回 0，在沙箱返回 2/ENOENT。glibc 主线程栈查询确实依赖自身 maps。
4. 为区分库依赖与整体环境问题，核对并下载官方同版本 musl 构建和 Ubuntu musl loader，
   仅在私有测试目录使用，未替换系统程序。它在相同文件隔离下 `--help` 成功。
5. 但真实 print/MCP 工作流仍失败：`Can't access working directory ... Path ... does not exist`。
   禁网最小复现实证：目录打开成功，随后 `readlink("/proc/self/fd/12")=ENOENT`。
   这是路径解析失败，不是目录真被删了；`--help` 成功不代表科研循环能运行。

边界：未取得 Bun 内部带符号的完整 abort 栈，不能声称已经定位到某一条内部 assert；
但自身进程元数据缺失、系统库调用失败、实际路径解析失败与运行不兼容均有独立实证。
没有依据指责 Claude 模型决策或 API 服务。独立容器也不能仅凭名字保证修复，须验证私有 procfs。

## 实际保留的代码修复

- `harnesses/sandbox.py` 在隔离探针中同时记录 `self_maps`、`self_fd`、专用临时目录可写性。
  只检查自身文件，不遍历或读取别的进程。
- Claude 缺失必需能力时写 `native/bootstrap.json`：
  `error_code=CLAUDE_PROCESS_METADATA_UNAVAILABLE`，并在预算时钟和模型请求前停止。
- 修正 `:tmpdir=deny` 与自身 `TMPDIR=actor-home/tmp` 的冲突。只恢复每 actor 专用 scratch；
  宿主 `/tmp`、项目私有数据、任务只读限制不变。
- 撤回未通过的 musl 默认配置及 loader 接口试验。两个 Claude YAML 仍指向用户原 `claude`，
  不静默换二进制、版本或模型。试验软件移出活动项目，保留在专用 /tmp 诊断目录中。

最后回归：161 passed / 9 native skipped（23.19 秒）；启用实际 Codex/Kimi 假 API 验收并排除
Claude 原生文件，168 passed（61.98 秒）。Claude 独立 `-x` 检查 1 failed（1.81 秒），
按新保护在明确缺少 `/proc/self/maps`、`/proc/self/fd` 时停止；这不是“Claude 通过”。

## 完整修复需要什么

优先提供独立 worker/VM 或调整外层容器部署，使其可以创建自己的 PID namespace＋procfs，
只挂载本任务输入/工作目录，凭据与评分仍留在宿主服务。不是把宿主 /proc 挂进去。
先重复无费用的私有文件拒绝、自身 proc 可用、CLI 启动、MCP 原始值、恢复和子 Agent 验收。
当前环境没有现成 Docker/Podman/Apptainer 命令；不承诺在原容器内换一个镜像就会解决宿主限制。

可选暂缓 Claude，用已通过的 Codex/Kimi 推进。不要为赶进度关闭文件隔离、伪造 maps/fd，
也不要把降级旧版 Node CLI 当同一 Harness 的透明修复。

## 复现材料与来源

私有本地原始日志：`local/diagnostics/claude-20260915/`，不随公开包分发。
这些只来自新建的清洁探针；未包含用户账号、科研结果或模型私有推理。
临时软件/探针：`/tmp/cfx-claude-diagnostic.MDhAx8/`，不在生产启动链上。
官方 musl 包 SHA-512 与 npm 元数据一致；Ubuntu loader SHA-256 为
`9f0883c20b4b746e05e947bafd99cb933f5494ffaaa6fcd360cbe1fbcf264883`。

核对 [glibc 2.39 源码](https://github.com/bminor/glibc/blob/release/2.39/master/nptl/pthread_getattr_np.c)、
[WebKit StackBounds](https://github.com/WebKit/WebKit/blob/main/Source/WTF/wtf/StackBounds.cpp)
及 [Codex 官方权限说明](https://learn.chatgpt.com/docs/permissions)；后者约束本轮不以放宽宿主读取作为修复。
本机 Claude npm 安装脚本确认当前 npm 包同样分发 Bun 原生二进制，重装 npm 并不会自动变成 Node 版本。
自动生成的 Bun crash-report 链接没有打开或外发。
