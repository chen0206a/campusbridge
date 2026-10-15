# D2 失败记录与改进项

## 开发中修复

1. 环境变量 `RATE_LIMIT_STATUS=503` 最初按字符串传入 Pydantic 的整数 Literal，专项首轮为 22 passed / 1 failed。改为环境入口显式转整数，配置测试与最终回归通过。
2. 第一轮 MySQL＋Redis 验收的功能项为 38 passed，但测试期间仍修改源码，导致 source_hashes_unchanged=false，driver 按约定返回失败。完整保留 `mysql-redis-acceptance/`。固定源码后新建 `mysql-redis-final/` 重跑：38 passed，source_hashes_unchanged=true，exit_code=0。正式验收采用后者。

## 故障注入结果

实际临时 Redis 7.0.15 的停止/重启、CLIENT PAUSE ALL，以及 TCP 无响应服务分别验证断连和超时：候选缓存回源、登录/消息限流 503，恢复后可重新计数。API 故障测试同时查询业务库，确认被限流拒绝的提交创建 0 个 AgentRun。

并发计数用 20 个线程发送 40 次调用，窗口额度 7，实际断言 7 次允许、33 次拦截；另一个账户独立计数，过期后重新允许。身份伪造、登录代理 Header、消息/SSE 共用预算均有 API 测试。

缓存损坏用错误文本、Hash、版本、source_id、绑定、重复候选、无效 JSON、其他校园有效来源模拟，均拒绝损坏项并回源。知识库自身 Hash 损坏则报告完整性错误，保留服务端证据校验。竞态测试通过确定性交错覆盖更新先于验证、检索中更新、验证后填充、持续更新等情况。

## 性能观察

BM25 的暖缓存 P50 与直接检索接近（4.37ms vs 4.36ms），首次 miss 则有额外成本。小知识库无需默认启用缓存；Dense/Hybrid 省去重复查询 Embedding，暖缓存 P50 分别下降约 82.7%/87.3%。完整逐次结果、P95 和资源值见 benchmark/results.json。

## 后续工程边界

- 当前内容快照指纹为 O(知识库大小)，适合本次 8 篇内置资料的实测规模；大规模目录应采用事务化 generation 与索引身份设计。
- 限流临时实例无 RDB/AOF，重启重置窗口；生产持久性、故障切换和内存分区策略留到部署阶段。运行中断连/超时始终失败关闭。
- Runtime 的 jobs/conversation_locks 和 D1 单 Runtime 保护保持原状，多实例任务执行未改造。
- 当前认证入口仍为 loopback 开发访问。未来反向代理接入时应显式设计可信代理和 peer-IP 获取，现阶段忽略 X-Forwarded-For。
- 保留一条既有 Starlette TestClient 弃用警告；本轮未升级 HTTP 测试栈。
- 本地 CI 配置已增加 Linux Redis job，尚未推送或触发远程 Actions。
