# Stage D2：Redis 检索候选缓存与入口限流

基线：Stage B `6ae84b7` → 已验收 D1 `2f5d2a7`。独立分支 `engineering/stage-d-redis`。本阶段增加基础设施适配，不调整 Agent、Prompt、任务编排、工单状态机或检索排序参数。

## 启用与本地模式

```bash
python -m pip install -r requirements.lock -r requirements-redis.lock
# MySQL 模式另装 requirements-mysql.lock；Dense/Hybrid 另装 requirements-rag.lock。
```

默认两个 Redis 开关均为 false；SQLite、MockModel 和现有 API 可以独立运行，甚至不安装 redis-py。

通过服务端环境设置以下配置，应用不自动读取 `.env`：

| 配置（均以 CAMPUSBRIDGE_ 开头） | 默认值 | 作用 |
|---|---|---|
| REDIS_CACHE_ENABLED / REDIS_RATE_LIMIT_ENABLED | false / false | 分开启用缓存和限流 |
| REDIS_URL | redis://127.0.0.1:6379/0 | 认证连接通过进程环境/secret manager 提供；远程连接使用 rediss |
| REDIS_NAMESPACE | campusbridge:d2 | 部署隔离；不同环境使用不同 namespace |
| REDIS_TIMEOUT / REDIS_MAX_CONNECTIONS | 0.15s / 32 | 连接、读写超时与连接池上限；关闭自动重试 |
| REDIS_CACHE_TTL | 120s | 候选和空结果的生存时间 |
| LOGIN_LIMIT / LOGIN_PEER_LIMIT / LOGIN_WINDOW_SECONDS | 10 / 100 / 60 | 账户、实际连接 IP 双重登录限制 |
| MESSAGE_LIMIT / MESSAGE_WINDOW_SECONDS | 20 / 60 | 每账户、校园的消息提交窗口 |
| RATE_LIMIT_STATUS | 429 | 超额状态可选 429/503；基础设施故障固定 503 |

`REDIS_URL` 使用 SecretStr，排除于 Settings 展示/导出，禁止通过 URL query 覆盖超时和重试配置。配置校验隐藏原始输入。启用缓存时要求两次 Redis 超时仍为 ContextAgent 留出预算。默认值的缓存失败最坏路径包含一次读、一次写超时；检索自身也需适当预算。

## 候选缓存和版本一致性

`CachedKnowledge` 包装现有 `SQLiteKnowledge` / `SemanticKnowledge`，维持 `search(query, campus_id)` 和 `source(source_id, actor)` 契约。MySQL 仍只保存业务数据，知识版本、FTS5 和 Embedding 内容缓存仍在 D1 的独立 SQLite 索引中。无需迁移业务表。

键为 `namespace:retrieval:SHA256(canonical dimensions)`，维度包括：

- 当前校园、查询前 500 字符的 NFC/空白规范化值（保留大小写，原检索使用同一规范化值）；
- BM25/Dense/Hybrid、Top-K、候选数、Dense 阈值、RRF 参数、显式回退策略；
- 实际 Embedding 内容身份和检索配置协议版本；
- 全知识库文档/块/FTS 内容的快照 SHA-256。包含所有校园是因为 BM25 的全局 IDF 受其他校园导入影响。

只保存候选字段与分数及配置绑定；不保存用户身份、会话、聊天回复、审核结果、工单或权限。键和 payload 都使用查询 SHA-256，不保存查询原文。知识文档候选是校园共享资料，缓存按 campus 隔离。Redis 运维需控制访问权限和备份范围；TTL 约束短期数据保留。

每次读执行：

1. 短 SQLite 只读事务取得一致的全局版本指纹。
2. 事务外请求 Redis；结构、协议绑定、条数异常视为无效缓存。
3. 再开启短只读事务，检查版本未变化，且候选 source_id 唯一、属于当前校园的 active 文档；逐项比对内容、实际文本 Hash、标题、版本、文档 ID、坐标和块序号。非有限分数无效。
4. 版本变化最多重试三次；持续更新时返回空证据，并记录 `cache_catalog_churn`。原检索结果也走同样的版本与来源检查再填缓存。

第二次快照是此次检索的**线性化点**。更新在此点之前提交，旧结果会重试；更新在此点之后提交，此次读取属于更新前的快照，下一次读取使用新键。填缓存期间发生更新，也只会写入旧版本键，后续读取不会命中它。测试覆盖读 Redis 时更新、原检索时更新、填缓存时更新、文档停用/再激活、连续版本变化。

当前小知识库直接计算内容指纹，避免在导入、手动停用和版本切换路径之间遗漏失效事件，同时识别独立索引的不同内容。代价是每次命中仍读取目录和文本，复杂度 O(知识库大小)。扩容时可迁移为数据库事务内更新的目录 generation，但需继续验证全局 BM25 依赖和索引分叉身份。

历史 source 查询维持原行为：允许当前校园查看曾经引用的历史版本，返回 `active_revision=false`；当前检索只接受 active 版本。候选之后继续进入原 Arbitrator、引用白名单、来源 Hash 校验、OutputReviewer 和交付校验。引用合法性和权限依据仍来自服务端和持久证据。

## 限流和故障策略

固定窗口由 Lua 在 Redis 原子执行 `INCR + PTTL + 首次 PEXPIRE`，返回唯一计数和 TTL。过期时间不会被后续请求延长；缺失 TTL 被修复。客户端根据唯一计数执行阈值判断。超额响应带 `Retry-After` 和 `Cache-Control: no-store`。

- 登录：先限制实际 TCP peer，再限制精确用户名。忽略 X-Forwarded-For；账户名大小写语义与认证一致。失败登录也计数。账户级防护会使同一账户的攻击流量影响其正常登录，这是账户防爆破策略的取舍。
- 消息：`messages` 和 `stream` 共用 `(服务端认证 campus_id, account_id)` 预算，客户端身份字段或伪造 Header 无效。限流发生在 enqueue 前，拒绝时不会创建 Run。重复 Idempotency-Key 仍算一次 HTTP 尝试；原有数据库幂等保证业务记录不重复。
- 缓存连接失败、超时、写入失败：记录技术计数，回源检索。嵌入模型缺失仍遵循原显式 fallback/error 设置，不能用 Redis 伪装已安装模型。
- 限流连接失败、池耗尽、超时、Redis/OOM 错误：固定 503，等待恢复；不使用进程内计数降级为无限放行，也不自动重试可能已执行的 Lua。

限流计数是有 TTL 的短期基础设施状态。测试实例禁用 RDB/AOF，重启会重新开启窗口。正式部署应为限流选择独立持久性/内存策略和受控重启流程；缓存可淘汰不代表限流键可随意淘汰。D2 临时实例统一 `noeviction`，内存满则限流失败关闭。断连后无法确认的请求可能消耗一次额度，但不会放大模型调用。

## 指标与复现

`app.state.redis_support.metrics.snapshot()` 返回线程安全聚合计数。客户端关闭时输出无查询、身份和密钥的 `REDIS_METRICS` JSON 日志。包括 hit/miss、无效候选、目录变化/竞态重试、回源失败计数、写入、过大条目、限流允许/拦截/不可用。命中率分母为缓存 hit+miss 的查找尝试，含重试；不是独立用户数。

真实 Redis 专项：

```bash
# 需可用 redis-server；测试自行创建独立端口、随机认证、无持久化临时实例。
python -X utf8 -m pytest tests/test_redis_d2.py -v
python -X utf8 -m pytest -q
python -X utf8 scripts/test_mysql_d1.py --mysqld /path/to/mysqld \
  --scratch /new/private/scratch --output /new/results \
  --extra-test tests/test_redis_mysql_d2.py
python -X utf8 scripts/benchmark_redis_d2.py --model /existing/local/bge-small-zh-v1.5 \
  --output /new/benchmark/results --repetitions 10
```

如果 redis-py 未安装，真实 Redis 测试明确 skip；安装后缺少服务端会失败，不会用 fakeredis 冒充成功。可通过 `CAMPUSBRIDGE_TEST_REDIS_COMMAND` JSON argv 指定 WSL 等服务端启动前缀。测试不会访问既有 Redis 服务，也不执行 FLUSHDB/FLUSHALL；凭据只在内存和服务端 stdin 中。

性能脚本固定六条查询，分别测量热模型/索引上的直接检索、第一次缓存 miss、暖候选缓存；保存逐次延迟、来源 ID、分位数、CPU 时间、RSS、Redis key memory/used_memory。使用本地真实 BGE CPU Embedding，未调用 LLM。单线程小库微基准不代表 HTTP 负载能力。

## 当前边界

Runtime 的 jobs、conversation_locks 仍在进程内，D1 单 Runtime 启动保护仍生效。Redis 缓存/限流可由多个客户端访问，但没有赋予业务 Runtime 多实例安全。本阶段不引入队列、Compose 或正式并发压测。

实现证据、全部结果和剩余问题见 [D2 HANDOFF](../experiments/stage-d2/20261015-v1/HANDOFF.md)。依赖来源：redis-py 7.4.1，MIT；本地验收 Redis 7.0.15，BSD-3-Clause，Ubuntu 二进制仅放外部临时目录。测试版本不作为正式部署版本推荐，部署应选择维护中的安全更新版本。

官方技术依据：[redis-py 生产连接与超时](https://redis.io/docs/latest/develop/clients/redis-py/produsage/)、[Redis INCR 限流示例](https://redis.io/docs/latest/commands/incr/)、[Lua 原子执行](https://redis.io/docs/latest/develop/programmability/eval-intro/)。
