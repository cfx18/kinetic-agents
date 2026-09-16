# 本轮重构验收（2026-09-15）

## 结论

新项目不依赖旧仓库的 Python 导入或实验目录。单／多组共享同一个输入目录，
只有运行产出、配置快照与 host-only 冻结材料进入新的时间戳目录。
科研任务与父机理未改；这是一轮工程验收，不是新的优化结果或 RSI 证据。

## 已检查

- 143 项单元／回归通过；默认跳过的两项原生验收另外显式执行。
- 原生 Codex CLI + 本机合成 Responses：单组、多组均通过；不访问真实模型。
- 单组不能创建子 Agent；多组主模型 Astra/xhigh，两个子模型 GPT-5.5/high。
- 工具原始返回在下一次模型决策前已写入 transcript；全组和逐角色记录保留。
- 公共任务确实可读且不可写；host canary、符号链接逃逸被拒绝；重启记录不串线。
- 构造、核验两组真实输入的 prepare/load，但没有启动预算时钟、账号 RPC 或 SSH。
- 8 个既有科学源哈希逐一与旧文件／评测 manifest 相等；数值字节未变。
- 独立 wheel 安装后在项目外导入 79 个模块，并成功构建、验证自身冻结源码。
- wheel 与 sdist 检查未包含 parent.yaml、local/ 数据、runs/、auth.json 或 runtime.sqlite。
- 远端提交使用合成 transport 验证 cfx、N1/n64、独立运行路径与显式镜像；没有远端实算。

## 本轮保留的负面发现

1. 共享 Ceph 元数据曾阻塞读文件、迁移和沙箱启动；从进程 wchan 确认，并非模型决策慢。
2. 原生多组测试首次在共享盘临时目录触发子会话等待超时。换成本机 scratch 后通过，
   未放宽行为断言或模型／资源配置。未来仍需在实际部署环境监控启动延迟。
3. 把公共输入也移进 /tmp 后，原生沙箱隐藏了该路径：不是放宽 errno 断言将其算过，
   而是改成生产的独立公共输入挂载，并增加明确读取任务的断言。正式 start 在计时前
   拒绝临时挂载内的公共输入。单／多组最终原生验收均通过。
4. 多进程 Black 进程池未退出；只终止本轮确认的格式化进程，不改科学文件、不触及实验。

## 未做／不可声称

- 无新模型费用、无真实模型生成、无 Slurm 科学任务、无新候选或分数。
- 不改变历史实验、旧 prepared/source-release、其他对话正在运行的任务。
- 不代表远端镜像永远可用；真实提交仍须校验既有镜像哈希和站点挂载。
- 不启用待审 60/40 grader；既有 610 池仍是历史暴露评测，不宣称真正盲测。
- 未发布；父机理、观测数据与第三方资源的再分发仍须逐项核对。

## 复现

在安装了 evaluation/test 依赖的 Python 3.12 环境中：

```bash
python -B -m pytest -q
RUN_NATIVE_TEAM_QUALIFICATION=1 python -B -m pytest -q \
  tests/test_subscription_native_pipeline.py tests/test_subscription_mixed_pipeline.py
python run.py validate --config configs/solo.yaml
python run.py validate --config configs/team.yaml
```

原生验收需本机 localhost 和 Codex 沙箱能力，真实账号不是前提。
源码映射见 migration-map.json；本机输入来源保存在被忽略的 local/input-provenance.json。
