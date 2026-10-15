# CampusBridge Stage D4 交接

2026-10-15。D3 已以非快进方式合并本地 main：`8f166091368c06bfc999629d42f497db1d4fcab1`，完整保留 Stage A/B/D1–D3 提交。D4 分支为 `engineering/stage-d-load`，未推送、未合并 D4。

## 范围与实现

按 D1/D3 已有清单实施单 Backend、真实 HTTP 的 Mock 并发、资源监测、工单/幂等/身份一致性与启动恢复。不增加 Agent，不改 Prompt、任务编排、审核或 RAG 排名。

- Locust 独立负载环境与固定版本；100 个虚构学生及隔离校园账户；10/30/50/100 VU，每档 45 秒，带 ramp/drain。
- 正文 Hash/SSE done 检查、来源 Hash、同键重放一致性；逐请求与逐端点记录；运行前冻结配置与源码。
- 独立 Compose overlay 提高测试限流额度、启用 MySQL 慢查询统计。默认限流未修改，缓存/限流仍启用。
- loopback-only `/ops/metrics`：SQLAlchemy pool、Runtime jobs/locks、Redis 计数/内存。前端代理不暴露该入口。Docker CPU/内存、MySQL 连接与行锁指标保存为原始样本。
- 新增 Docker Redis stdin 测试夹具，支持 Windows 上真实 Redis 回归，不接触既有 Redis 服务。

## HTTP 负载实测

| VU | 请求数 | RPS | P50 | P95 | 错误/超时 |
|---|---|---|---|---|---|
| 10 | 567 | 12.47 | 445.54ms | 1,494.57ms | 0/0 |
| 30 | 649 | 14.01 | 1,524.01ms | 4,836.13ms | 0/0 |
| 50 | 671 | 13.17 | 2,662.23ms | 8,381.96ms | 0/0 |
| 100 | 909 | 15.44 | 3,418.91ms | 15,750.92ms | 0/0 |

2,796 条样本包含登录、建立会话、查询、RAG/SSE 和幂等重放。前置正确性阶段有 4 个 Run，负载结束累计 805 个 ACCEPTED Run，即负载新增 801 个；没有重复 Run/消息/工单/调用索引或自动兜底。每档都验证全部目标 VU 登录，所有原始失败/耗时纳入统计。

冷暖两个真实聊天请求分别 280.02ms /202.71ms，miss→hit 且引用一致；这是两次完整 turn，不作检索微基准。最后一档累计缓存 288 hit/17 miss，命中率 94.43%；过期/更新重试保持记录。100 VU 连接池采样 checked-out 最大 10，MySQL Threads_connected 观察最大 22（包含运维等连接）；Slow_queries 全档累计 1，无观察到的增量。资源详情见 analysis/report.json。24 逻辑 CPU、约 7.64GiB Docker 内存，负载生成器与 Docker 同机。

100 VU 有 2 次独立监控采集失败，最后日志为 5s ops 读取超时；业务请求零超时。吞吐趋于平稳且尾延迟上升，这是当前单实例的短时容量记录，后续应先剖析排队热路径。

## 正确性与恢复

- 16 个同键 HTTP 消息请求：仅 1 Run、1 助手消息；重启后相同键不新增 Run/ModelCall。
- 16 路同工单认领：1 成功、15 明确冲突；错误 owner/学生角色/跨校园读写被拒绝。后续处理关闭完成，审计保留。
- Redis 停止：消息 503，新增 Run 为 0；Redis 恢复后可继续。
- 控制持久化夹具：未完成 Run、STARTED Call、RiskEvent/工单；重启后 Run FAILED/SERVER_RESTARTED、Call INTERRUPTED、工单 OPEN 保留、记录数量不变。backend 恢复 9.16s。此处是状态夹具，未模拟真实模型生成中断。
- 默认 Compose 新项目复验通过：空数据卷且已有构建缓存，50.46s；backend+Redis 重启 7.16s；down 保留卷再 up 23.24s，迁移、知识版本/cache 失效、HTTP/SSE/Trace、权限幂等和全部工单状态通过。
- 浏览器独立 8081：来源详情、刷新恢复、工单 CB-74ECDD 从 OPEN 到 CLOSED、版本 3、4 条历史通过。8080 原演示环境保持原样。

## 回归与完整性

340 passed /20 opt-in skipped，119.12s；真实 MySQL 8.0.42 + Docker Redis 7.2 专项 38 passed，覆盖全部 20 项，合计 360 个独立后端测试通过。Linux 容器安装包 46 passed；前端 27 passed、TypeScript 与生产构建通过。10 项 D4 新测试覆盖本机指标隔离、SSE 完成与 Hash、统计分位数、负载配置及 Docker Redis 密码处理。

775 个既有 experiments/eval/Prompt/知识文件 SHA-256 不变；原始 archive verifier 的 560 项通过。D3 历史记录没有重写。正式运行过程中冻结源码校验通过；运行结束后只调整 release 导出允许列表、增加测试/报告工具等非业务文件，关键运行源码保持绑定。

## 复现与后续

[实施与命令](../../../docs/STAGE_D4_RELIABILITY.md)、`CHANGED_FILES.json`、`http-load-v1/protocol.json`、`source-binding.json`、逐请求 CSV/JSONL、全部测试日志及失败记录均在本次提交中。用 `python scripts/report_load_d4.py --input experiments/stage-d4/20261015-v1/http-load-v1 --output /new/report` 可离线重算指标。Windows pytest 指定 Docker Redis 命令见实施文档；MySQL scratch 使用新的 ASCII 路径。

本次没有多 Backend、分布式队列或自动接管；进程内 conversation_locks 数量会随会话增长，本轮仅短时验证。建议下一步先做同步数据库/事件循环剖析、会话锁生命周期和持续负载观察，再决定跨进程任务租约/fencing 设计。没有运行付费模型、LoRA 或自动发布。

简历可用表述：在单实例 FastAPI/MySQL/Redis/Compose 架构上建立真实 HTTP 并发验收；2,796 次 Mock 请求零失败，16 路工单竞争仅 1 次成功，验证同键请求仅一份业务记录及重启后风险工单保留，公开逐请求与资源证据。
