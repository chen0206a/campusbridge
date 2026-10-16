# Stage D5：性能证据与有限优化

基于本地 D4 合并 `e8cbaa2`。D4 的全部原始 HTTP/资源结果保持原样；D5 使用相同 Locust `CampusUser` 工作负载、Mock、单 Backend、MySQL/Redis、BM25、限流额度、45 秒各档、10 VU/s 与 30 秒 drain。新的合成数据库和知识卷按项目独立创建，不复用开发数据。

## 采集

`CAMPUSBRIDGE_PERFORMANCE_ENABLED=true` 显式启用，默认关闭。不保存 SQL 文本、用户内容、账号 token 或凭据。

- 50ms event-loop heartbeat：记录实际唤醒延迟。
- Store 各方法 wall time 与调用线程是否为 event loop。
- SQL execute、完整写事务（含 commit）、pool acquire 和实际等待 QueuePool 队列的时间。acquire 包含建连；queue wait 单独测量。SQLAlchemy 2.0 的可选诊断 hook 绑定当前锁定版本，不改变池容量。
- 会话锁等待/持有、各 Agent collect_step 耗时；不改变任务路由或预算。
- Docker CPU/内存、MySQL 连接/慢查询/行锁、后端线程数。
- Locust 生成器 CPU/RSS/线程及 gevent hub lag，检查客户端自身的排队。

每个 timer 的 count/sum/max 为进程累计，分位数取最近最多 8192 个样本。各计时有嵌套，不把 SQL、事务与 Store 时长相加。HTTP P50/P95/P99 直接从全部原始请求复算，包含失败、登录、ramp 和 drain。

## 监控采集可靠性

D4 的运维 HTTP 入口与业务共享 event loop，100 VU 下曾出现读取超时。D5 的独立导出线程每秒在容器 tmpfs 原子替换一份无敏感内容的快照；负载生成器通过 Docker exec 读取文件，而不排队调用 HTTP。event-loop lag 仍由事件循环协程测量，不用导出线程的活跃性替代它。

每轮分别收集 Docker/app/MySQL，任何组件失败也保留其他组件；逐行 `resources.jsonl` 立即 flush，结束时另存 JSON 数组。记录组件错误与快照年龄，不能用“没有抛异常”替代完整性检查。该文件仅为监测数据，不参与授权、审核、交付或恢复判定。

## 顺序与复现

先完成诊断提交 `e26fa67` 的四档 instrumented baseline，保存分析结果；随后才做小范围优化，再用相同诊断配置跑四档。历史 D4 作为额外对照，主要因果比较使用本次同环境的前后配对。一次短时运行不作为生产 SLA。

```bash
python scripts/run_load_d4.py --root . --output /new/external/profile-results --project cb-d4-d5-new --load-python /external/load-venv/bin/python --port 8083 --duration 45 --profile
python scripts/report_performance_d5.py --baseline /external/baseline --optimized /external/optimized --historical experiments/stage-d4/20261015-v1/http-load-v1 --output /new/comparison
```

Windows 使用负载环境的 `Scripts/python.exe`；可指定 `--docker`。正式负载期间不并行跑 pytest/npm。项目切换只执行专用测试项目的普通 `down`，保留所有数据卷。实际瓶颈、采用的优化、对照数值、回归与剩余问题记录在独立 D5 HANDOFF 中。
