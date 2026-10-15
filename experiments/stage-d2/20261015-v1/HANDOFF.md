# CampusBridge Stage D2 HANDOFF

基线 D1 `2f5d2a7`（包含 Stage B `6ae84b7`），本地分支 `engineering/stage-d-redis`，2026-10-15。只交付 Redis 集成，未推送、未合并 main，未进入 D3/D4。

## 已实现

- 可独立配置的 Redis 候选缓存与原子限流，默认关闭，可不安装 Redis 客户端运行。
- BM25/Dense/Hybrid 共用缓存适配。键绑定校园、规范化查询 Hash、Top-K、知识内容版本、实际 Embedding 身份和检索配置。
- 两次一致性快照、命中来源全字段/Hash 校验、有限版本重试；缓存错误旁路，目录持续变动返回空证据。
- 登录使用实际 peer＋精确账户名双层限制，消息/SSE 使用服务端认证身份共用额度。Redis 失败固定 503，超额默认 429，附 Retry-After。
- TTL 管理候选、空结果和窗口计数；聚合 metrics 与 REDIS_METRICS 结构化日志，无查询原文、身份或密钥。
- 本轮未改 Agent/Prompt、Runtime/编排、RAG 排序/阈值、工单逻辑和数据库结构。

## 测试验收

| 验证 | 实际结果 | 原始证据 |
|---|---|---|
| 全后端（含 Redis 专项） | **314 passed / 20 skipped / 0 failures / 0 errors**，247.50s | backend-regression/backend-final.txt、XML |
| 私有 MySQL 8.0.42＋Redis 7.0.15 | **38 passed / 0 skipped / 0 failures/errors**，97.96s，driver 102.66s | mysql-redis-final/ |
| D2 专项 | **32 passed**（30 项实际 Redis 路径、2 项离线配置/关闭测试） | 全后端 XML 的 test_redis_d2 |
| 安全发布规则补验 | **10 passed**；Redis lock 可导出，RDB/AOF/.redis 排除通过 | release-tests.txt |
| 无 Redis 客户端的安装包体验 | Swagger 200、Mock ACCEPTED；隔离 wheel 导入 | WHEEL_VERIFICATION.json |
| 历史保护 | **704/704 文件 byte SHA-256 不变**；既有归档索引 560 文件验证通过 | HISTORICAL_INTEGRITY.json |

全后端跳过的 20 项是 opt-in MySQL 项，已在专项中实际执行。两套结果按 testcase ID 去重共 **334 项通过**，D2 新增 **33 项**（含 MySQL＋Redis 共存）。保留既有 Starlette TestClient 弃用警告 1 条。

并发验证：20 线程、40 次原子计数请求、额度 7，断言允许 7 / 拦截 33；窗口到期后恢复，其他账户隔离。API 在 Redis 不可用时拒绝提交，业务库检查 0 个新 Run。故障包含真实 Redis 停止/重启、CLIENT PAUSE ALL、无响应 TCP 超时；缓存均回源，限流失败关闭。

竞态验证：命中读取中版本变更、回源检索中变更、校验后填缓存变更、持续版本变化；并覆盖历史来源查询、校园隔离、停用/激活、错误来源/Hash/JSON/重复候选和引用校验。

## 缓存延迟实测

Windows 11 / Python 3.12.15，WSL 独立 Redis 7.0.15，本地 BGE-small-zh-v1.5 CPU，2 线程。8 篇内置原创校园文档；固定 6 条查询含无结果查询。每模式直接检索和暖缓存各 60 次、冷缓存 miss 6 次；模型/文档索引已热。P95 使用 nearest-rank，冷缓存样本少，P95 即本组最大值。

| 模式 | 直接检索 P50 / P95 ms | 冷缓存 miss P50 / P95 ms | 暖缓存 P50 / P95 ms | 暖 P50 降幅 |
|---|---:|---:|---:|---:|
| bm25 | 4.36 / 7.66 | 9.71 / 15.15 | 4.37 / 5.33 | -0.3% |
| dense | 30.50 / 33.52 | 36.88 / 38.05 | 5.28 / 6.22 | 82.7% |
| hybrid | 35.00 / 38.62 | 38.52 / 40.38 | 4.45 / 5.16 | 87.3% |

每模式 6 miss＋60 hit，整体命中率 **90.91%**，暖阶段 **60/60=100%**。这是固定重复查询工作负载的实测命中率。缓存前后全部来源 ID 序列一致，逐次 query/latency/source_id、配置和源码 Hash 见 [benchmark/results.json](benchmark/results.json)。未调用 LLM，也未执行正式 HTTP 压测。

**建议：保留 BM25 与 Redis 缓存默认关闭。** 本次 BM25 暖 P50 几乎持平，首次 miss 增加开销；有热点重复查询的 Dense/Hybrid 可按需启用。检索模式推荐仍沿用 Stage B 的数据依据，本阶段没有重新评估检索质量。

## 资源

- 18 个候选缓存键共 **23,312 bytes（22.77 KiB）** Redis key memory。
- Redis used_memory **1,097,424 bytes**，峰值 **1,141,520 bytes**，临时实例内存上限 64MiB/noeviction；包括连接和分配器，不能等同 payload 大小。
- Embedding 首次加载 **18.77s**；首次 Dense 文档索引构建 **0.650s**；Hybrid 复用已有 8 条文档向量。
- 首次模型加载前后进程 RSS 差 **260,009,984 bytes（247.96 MiB）**，含现有 Torch/模型加载；进程峰值 RSS **382.69 MiB**。这是现有 Embedding 成本，不是 Redis 常驻内存。
- 各模式缓存阶段 RSS 差含系统工作集回收，可能为负；逐项原值和 CPU 时间保留在 JSON，未将差值当成稳定内存归因。

## 关键文件

| 文件 | 改动 |
|---|---|
| src/campusbridge/redis_support.py | Redis 连接、统一缓存读写、计数 Lua、失败策略、聚合指标 |
| src/campusbridge/retrieval_cache.py | 候选封装、版本指纹、校园/来源校验、竞态重试 |
| src/campusbridge/api.py | 适配挂载、登录和两种消息入口限流、错误响应、清理 |
| src/campusbridge/settings.py / .env.example | 开关、TTL、连接池/超时、限流配置、SecretStr |
| scripts/serve_rag.py | 缓存包装下继续预热原 SemanticKnowledge |
| tests/test_redis_d2.py | 32 项专项验证 |
| tests/test_redis_mysql_d2.py | 实际 MySQL/Redis/RAG/风险工单共存 |
| scripts/redis_test_server.py | 独立临时 Redis 生命周期；密码仅内存/stdin |
| scripts/benchmark_redis_d2.py | 真实 Embedding/Redis 微基准与逐次结果 |
| scripts/test_mysql_d1.py | 添加可选 extra-test，不改历史结果 |
| scripts/release.py / .gitignore | 导出 Redis lock，排除快照和缓存 |
| pyproject.toml / requirements-redis.lock | 可选 redis-py 7.4.1（MIT） |
| .github/workflows/ci.yml | 新增 Linux 实际 Redis 专项 job（尚未推送执行） |
| README* / THIRD_PARTY_NOTICES / docs/STAGE_D2_REDIS.md | 配置、设计、来源和复现 |

完整文件清单见 CHANGED_FILES.json；本轮新结果/报告的字节 Hash 见 SHA256SUMS.txt，新增/修改代码文档见 CODE_SHA256SUMS.txt。

## 复现命令

```bash
python -m pip install -r requirements.lock -r requirements-redis.lock -r requirements-mysql.lock
python -m pip install -e . --no-deps
python -X utf8 -m pytest -q
python -X utf8 scripts/test_mysql_d1.py --mysqld /path/to/mysqld --scratch /new/private/scratch --output /new/results --extra-test tests/test_redis_mysql_d2.py
# CPU 微基准另装 requirements-rag.lock，使用已有本地模型，不下载权重。
python -X utf8 scripts/benchmark_redis_d2.py --model /existing/local/bge-small-zh-v1.5 --output /new/benchmark --repetitions 10
python -X utf8 scripts/verify_archive.py
python -X utf8 scripts/release.py audit --root .
```

本地测试通过 CAMPUSBRIDGE_TEST_REDIS_COMMAND 指向 WSL 独立 Redis 二进制，服务仅绑定回环随机端口，无 RDB/AOF，随机认证保留在内存。没有修改现有 MySQL/Redis 系统服务。两类服务均已由测试脚本停止。

## 剩余事项

详细故障记录见 [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md)。目录指纹当前为 O(知识库大小)；限流生产持久性/淘汰策略、可信代理设置和多实例 Runtime 属于后续部署设计。jobs/conversation_locks 仍在进程内，D1 单实例保护保持生效。D2 到此停止。
