# CampusBridge D5 交接

2026-10-16；分支 `engineering/stage-d-profile`。D4 已非快进合并本地 main：`e8cbaa29b7e14fa7c840621d0b2f6f94a781d0c9`，完整保留此前历史。D5 尚未合并、未推送。

## 先剖析，再优化

诊断提交 `e26fa67` 先跑完四档并归档 `PROFILE_FINDINGS_BEFORE_OPTIMIZATION.json`；其记录分析 SHA-256 和优化前保存时间。随后实现审计优化，正式执行提交 `3230d31`，再跑相同四档；没有按结果调阈值或挑选重跑。

剖析显示 100 VU 时 event-loop lag P99 1295.01ms/max 2129.40ms，业务 SQL 的 172,393/185,590 次执行在事件循环线程。Store 审计累计 70.38s、重复 run_snapshot 35.00s；单次写事务均值 4.25ms。连接池累计队列等待仅 1.52s，SQL 慢查询无观察到的负载增量。会话锁等待均值 0.0017ms，持有时间长主要反映整轮执行。Locust 平均 CPU 10.98%、hub lag P99 13.67ms，客户端未显示主要饱和迹象。

## 采用的改动

- Dynamic 审计在已有 Run 行锁内绑定当前状态，移除额外状态查询。
- 将连续同步的仲裁 start/decision/finish/plan-update 合为 `audit_many`：一次锁定和序号查询，原子写入全部事件。只合并元数据，不跨 await 或风险/审核/交付事务；异常保留部分 Trace，写入失败整体回滚。Sequential 行为保持原样。
- 可选 profiler 记录 event loop、SQL/池/事务、会话锁、Agent 阶段与线程。独立导出线程每秒原子刷新 tmpfs 快照，Docker exec 读取，监控不再排队等待业务 HTTP。各组件独立采样，JSONL 逐次 flush，保留部分结果与错误。

两轮配对资源同为 24 逻辑 CPU/8,204,206,080 字节 Docker 内存；相同 Compose/overlay、池容量、Redis、Mock、知识和原始 Locust workload Hash。执行源码只在 runtime/store/相应 profiler 标签处有差异，见 PAIRED_CONDITIONS.json。历史 D4 另列为对照；主要原因判断使用同日配对。

## HTTP 指标

| Run | VU | 请求 | RPS | P50 ms | P95 ms | P99 ms | 失败/超时 |
|---|---|---|---|---|---|---|---|
| historical_d4 | 10 | 567 | 12.47 | 445.54 | 1494.57 | 2275.25 | 0/0 |
| historical_d4 | 30 | 649 | 14.01 | 1524.01 | 4836.13 | 5288.27 | 0/0 |
| historical_d4 | 50 | 671 | 13.17 | 2662.23 | 8381.96 | 9084.96 | 0/0 |
| historical_d4 | 100 | 909 | 15.44 | 3418.91 | 15750.92 | 16311.95 | 0/0 |
| baseline | 10 | 536 | 11.97 | 465.29 | 1667.40 | 2265.69 | 0/0 |
| baseline | 30 | 626 | 12.76 | 1444.53 | 5257.14 | 5757.37 | 0/0 |
| baseline | 50 | 677 | 13.65 | 2472.32 | 7889.86 | 9725.47 | 0/0 |
| baseline | 100 | 890 | 15.08 | 3519.48 | 15293.82 | 16617.21 | 0/0 |
| optimized | 10 | 648 | 14.27 | 359.63 | 1301.75 | 1720.00 | 0/0 |
| optimized | 30 | 719 | 15.68 | 1210.33 | 4224.10 | 4728.85 | 0/0 |
| optimized | 50 | 799 | 16.37 | 2149.59 | 6761.50 | 7203.64 | 0/0 |
| optimized | 100 | 977 | 18.14 | 2905.82 | 12986.30 | 13791.20 | 0/0 |

100 VU 同日配对：RPS **+20.26%**，P95 **-15.09%**，P99 **-17.01%**。D5 正式样本 3143 条零失败。HTTP 分位数包含全部请求、登录、ramp 和 drain；这是同机单次 Mock 短时负载，不是真实 LLM 推理吞吐。

平均业务 SQL/Run：239.78→175.51；写事务/Run：45.38→40.30；run_snapshot/Run：28.10→3.52。记录数量按实际 Run 归一化，并保留原始计数。SQL、Store 和事务为嵌套计时，不相加。

## 资源、数据库与阶段耗时

| Run | VU | Backend CPU 采样峰值 % | 内存采样峰值 MiB | Python 线程峰值 | pool queue wait 累计 ms |
|---|---|---|---|---|---|
| baseline | 10 | 92.79 | 88.39 | 32 | 0.00 |
| baseline | 30 | 135.71 | 110.20 | 53 | 2.06 |
| baseline | 50 | 113.82 | 114.00 | 58 | 523.33 |
| baseline | 100 | 161.74 | 120.30 | 70 | 1518.34 |
| optimized | 10 | 88.64 | 95.39 | 27 | 0.00 |
| optimized | 30 | 107.90 | 108.30 | 48 | 0.00 |
| optimized | 50 | 109.08 | 116.00 | 63 | 318.31 |
| optimized | 100 | 164.53 | 121.00 | 65 | 918.71 |

CPU 100% 表示一个逻辑核；资源峰值是离散采样。服务侧 JSON 的 rss_max_bytes 字段来自 Docker stats MemUsage，表示容器内存用量，客户端同名字段才是 psutil 进程 RSS。queue wait 与包含建连的 acquire 分开测量。pool timer count/sum/max 为进程累计，内部 timer 分位数为最近最多 8192 个样本；HTTP 分位数使用全部样本。

| Agent 阶段 | 基线 P95 ms | D5 P95 ms |
|---|---|---|
| IntentAgent | 1743.94 | 1264.73 |
| RiskAgent | 1746.25 | 1267.40 |
| ContextAgent | 2364.73 | 1969.83 |
| ResponseAgent | 1657.60 | 1255.36 |
| OutputReviewer | 1631.11 | 1256.08 |

阶段 wall time 包含调度和数据库等待，不当作 Mock 计算或真实 LLM 延迟。Delivery 由 store.commit_reply 计时，完整原始 timer 和每档客户端 CPU/RSS/hub lag 见 comparison/report.json。

## 监控修复验收

D4 历史 100 VU 有两次监控失败。本轮未优化基线和优化版的全部八档均 0 组件错误；分别 43/41 个采样记录，JSONL 行数与最终数组完全一致，Docker/app/MySQL 数据均保留。快照年龄最大分别 0.996s/0.980s，未发生导出失败。未优化基线也能完整采集，证明修复来自独立导出和部分结果持久化，而非只靠业务提速。

## 可靠性与测试

- 两轮均通过 16 路同工单 1 成功/15 冲突、16 路同键仅 1 Run/助手消息、SSE done 与正文 Hash、来源 Hash、学生/校园/工单 owner 权限检查。
- Redis 停止时消息 503、0 新 Run；重启后恢复。未完成状态夹具重启后 FAILED/SERVER_RESTARTED、Call INTERRUPTED，风险工单保留，重复键不新增执行。夹具不声称是真实模型生成中断。
- 两条实际风险 Trace 通过原有 verify_task_trace；真实 MySQL 新增 16 路审计批次、64 个事件检查连续序号与批次原子性。
- 后端 **349 passed/21 opt-in skipped**，237.01s；真实 MySQL 8.0.42 + Redis 专项 **39 passed**，70.27s，覆盖全部 21 项，另 18 项重叠，合计 **370 个独立后端测试通过**。Linux 安装包 **55 passed**；前端 **27 passed**、TypeScript 与生产构建通过。
- 默认 Compose 新项目通过迁移、HTTP/SSE/工单闭环与持久化：空卷且已有镜像缓存启动 45.38s，backend/Redis 重启 7.08s，down 保留卷再 up 24.37s。Redis AOF 额度 count=1、TTL 600→598，剩余两次允许，第四次 429；缓存命中引用一致。
- 容器安装的 45 个源码/资源文件与工作树逐字节一致；MySQL 测试源码绑定一致。862 个历史实验/eval/Prompt/知识文件 Hash 不变，旧 archive verifier 560 项通过。没有删除现有数据卷或开发数据库。

## 发布收尾与复现

[剖析与执行命令](../../../docs/STAGE_D5_PERFORMANCE.md)、CHANGED_FILES.json、CODE_SHA256SUMS.txt、全部测试日志、原始逐请求/资源/客户端数据、两版协议和源码 Hash 均已归档。默认不启用 profiler；无新增 Agent、模型训练、付费模型请求或基础服务。README/架构说明已更新，公开文件审计单独记录。最终净源码导出使用 `python scripts/release.py build --root . --output /new/external/release`，不改写历史发布包。

## 剩余问题

event-loop lag P99 仍约 1000.48ms，100 VU HTTP P95 仍约 12.99s。同步 Store/SSE 重复读取依然占用主线程，未声称已消除阻塞；后续可独立评估显式异步持久化边界。会话锁等待不是本轮主要瓶颈，字典的长期生命周期与持续负载仍待单独验证。只有一个 Backend，未实现分布式任务接管。SQLAlchemy 私有队列诊断 hook 绑定当前版本，升级时须回归。

本轮没有失败的正式负载或选择更好重跑，保留全部观测。继承的 Starlette/httpx 弃用警告仍存在。D5 到此停止，等待验收。
