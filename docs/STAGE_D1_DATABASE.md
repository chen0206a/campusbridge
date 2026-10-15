# Stage D1：业务 MySQL 与独立知识索引

起点为Stage B稳定提交6ae84b7，独立分支engineering/stage-d-mysql。本轮实现D1；Redis、Compose和Locust/k6留在后续阶段。

## 部署边界

| 组件 | 存储/职责 |
|---|---|
| 单个FastAPI Runtime | 原TaskPlanner、Arbitrator、Agent、审核后SSE与进程内conversation_locks/jobs |
| 业务数据库 | SQLite轻量模式，或MySQL8/InnoDB；11张业务表，由Alembic迁移 |
| 知识数据库 | 独立SQLite文件：文档、全部版本、chunk、FTS5、可选embedding_cache |
| ModelPort | Mock默认，已有真实Provider可配置；模型没有数据库连接或状态写入权限 |

ContextAgent继续使用KnowledgePort。BM25/Dense/Hybrid实现与Prompt不变，MySQL不承载FTS SQL。知识导入依旧在知识SQLite内原子维护版本和FTS；Dense从active集合选择证据并按内容补充缓存。历史来源仍可查询停用版本。RunEvent在业务库保存本轮引用快照，最终审核与candidate/hash绑定继续校验。

同一后端实例使用一份明确的知识索引路径。两个存储之间没有分布式事务：知识导入独立完成后才可检索，新请求读取有效版本，已有回复按已保存的引用快照追溯。

## Schema与迁移

Alembic迁移位于src/campusbridge/migrations/，随wheel安装，不依赖工作目录或明文连接配置文件。

- d1_0001：创建accounts、login_tokens、students、conversations、agent_runs、messages、model_calls、risk_events、run_events、support_cases、case_events。使用冻结的business-schema-v1.json，不从未来ORM自动构造历史迁移。
- d1_0002：新增唯一索引messages(conversation_id,order_index)，以及support_cases(campus_id,state,created_at)、agent_runs(state,created_at)查询索引。
- d1_0003：MySQL中candidate、approval、RunEvent.event、RiskEvent.assessment改用LONGTEXT＋JSON_VALID约束；SQLite继续JSON。ExactJSON负责严格序列化/反序列化，避免MySQL原生JSON回写Trace浮点数而改变内容Hash。
- 原Run幂等(student_id,operation_key)、Call(run_id,call_index)、事件(run_id,sequence)、RiskEvent(run_id)、SupportCase(risk_event_id)、CaseEvent(case_id,operation_key)、Message(run_id,role)约束保留。

MySQL表使用InnoDB、utf8mb4、utf8mb4_0900_bin。NO PAD二进制collation保留幂等键大小写和尾随空格语义，行为与SQLite一致。Token到期时间及调用耗时使用DOUBLE，避免MySQL FLOAT对Unix时间的精度损失。

MySQL应用启动只验证Alembic head与列、外键、唯一约束、索引和表选项，不执行DDL。迁移使用独立账号；验收中应用账号只有SELECT/INSERT/UPDATE/DELETE。SQLite应用自动升级新建库；已有未版本化库必须显式adopt，经结构、外键和消息顺序重复检查后才允许stamp并升级。

## 事务与并发

| 操作 | MySQL事务内的锁与约束 |
|---|---|
| enqueue | 先锁学生Account父行，再锁Conversation；检查已提交幂等键及request_hash；唯一约束作为最终防线，覆盖跨会话键复用 |
| begin / commit_reply | Conversation→AgentRun顺序锁定；消息顺序唯一索引；用户消息/状态/事件或回复/审批/终态/Delivery事件同事务提交 |
| ModelCall与usage | AgentRun→ModelCall顺序锁；预算、调用索引和usage在短事务内更新；已完成调用再次finish不累计 |
| Trace | 持有Run行锁后分配max(sequence)+1；数据库(run_id,sequence)唯一 |
| 风险关注 | 锁Run；风险事件、工单、CREATE审计与RunEvent同事务提交；唯一关联确保重复调用复用同一事项 |
| 工单操作 | SELECT FOR UPDATE锁工单；先检查幂等历史，再检查expected_version、状态和owner；状态/版本/CaseEvent同事务提交 |

SQLite写事务仍使用BEGIN IMMEDIATE；MySQL使用READ COMMITTED及行锁。每个操作拥有独立Session，模型await期间没有数据库事务。

只对InnoDB 1205/1213执行最多3次完整事务重试，带短退避；耗尽返回DATABASE_WRITE_CONFLICT。连接丢失这类提交状态不明的错误不自动重放。调用方以原Idempotency-Key查询/重试，数据库仍是业务事实来源。

连接池默认10、overflow10，pre_ping=true，recycle1800s，pool_timeout5s、connect5s、读写10s；会话innodb_lock_wait_timeout=5s，IO timeout配置至少6s。另有1条独立连接用于Runtime所有权，按21条最大连接预算规划单实例。

## 运行

SQLite默认体验：

```bash
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python scripts/serve_rag.py --port 8014
```

MySQL可选依赖：

```bash
python -m pip install -r requirements-mysql.lock
```

通过受保护的进程环境提供迁移账号URL到CAMPUSBRIDGE_MIGRATION_DATABASE_URL、应用DML账号URL到CAMPUSBRIDGE_DATABASE_URL，驱动为mysql+pymysql，库须事先建立。URL不出现在CLI参数、alembic.ini或报告中。远程生产连接的TLS、秘密管理与备份策略由部署阶段落实；当前真实验收使用loopback私有MySQL8.0.42。

```bash
python scripts/migrate_database.py upgrade
python scripts/migrate_database.py verify
python scripts/serve_rag.py --port 8014
```

指定CAMPUSBRIDGE_KNOWLEDGE_DATABASE_PATH为可写SQLite索引文件；默认值为本地业务路径的同目录.knowledge.sqlite3文件。保留CAMPUSBRIDGE_MODEL_PROVIDER=mock即可完整体验，LLM配置与前阶段一致。

## 从旧SQLite切换

先停止旧应用并备份原文件。工具会取得同一Runtime文件锁，拒绝复制仍在本应用运行中的源库；源数据用只读一致性快照访问。MySQL目标须为空，复制保留所有ID、Hash、幂等键、JSON产物与状态，事务中逐表核对规范化内容Hash。

```bash
python scripts/migrate_database.py split-knowledge --sqlite data/legacy.sqlite3 --knowledge-target data/knowledge.sqlite3
python scripts/migrate_database.py upgrade
python scripts/migrate_database.py copy-business --sqlite data/legacy.sqlite3
```

知识复制保留全部停用/当前版本及source_id，重建FTS；向量缓存按原内容重新构建。新知识库不复制账号、消息或Token。所有参数中的data目录仅为操作示例，应指向备份后准备迁移的文件。

如果继续使用旧SQLite业务库，先取消迁移URL环境变量，再执行：

```bash
python scripts/migrate_database.py adopt-legacy-sqlite --sqlite data/legacy.sqlite3
```

设置业务路径与知识路径后启动。旧业务库保留的知识表不会被删除；启动会检查拆分来源证明，避免漏拆后默默退回空知识库。已有数据Schema不匹配或外键受损时停止，先人工处理后另行迁移；MySQL DDL会隐式提交，生产迁移前须有快照与恢复窗口。

## 单实例与恢复

MySQL使用数据库作用域GET_LOCK，由专用连接持有；第二个Runtime启动失败。写事务前检查锁是否仍属于本连接，连接/所有权丢失后停止写入；需重启应用重新获得所有权并恢复。SQLite用OS文件锁提供同一文件的单Runtime保护。退出后的迟到写入会被拒绝。

启动恢复将非终态Run标记FAILED/SERVER_RESTARTED，将STARTED调用标记INTERRUPTED，保留已提交工单、风险记录及历史回复，不自动重放模型调用。

jobs、conversation_locks仍在进程内。当前验证的是单实例多请求并发，启动保护不是分布式任务调度。多实例需另行实现持久任务认领、conversation lease/fencing、幂等执行、租约恢复及跨实例知识版本发布；本轮不开放多worker，也不提供自动故障转移。

## 风险清单与后续边界

1. MySQL DDL非事务回滚；先备份，逐revision验证，失败时不盲目stamp。
2. 旧SQLite无版本信息；仅结构匹配且完整性检查通过才显式接管，损坏数据不会自动“修好”。
3. 数据切换需要停止源Runtime；迁移后非终态Run由启动恢复处理，幂等键仍绑定原请求。
4. 业务库和知识库没有跨库原子提交，引用使用本轮持久快照；知识更新只影响后续检索。
5. 死锁/锁等待有限重试，连接故障显式失败；同步数据库调用的高负载延迟留待D4测量。
6. 专用Runtime连接失效需要应用重启；尚无分布式Worker或跨节点知识索引协调。
7. 当前认证入口仍有原loopback限制；容器代理入口、TLS、密钥与持久卷在D3单独验证。
8. D1报告是事务正确性与集成验收，不包含Locust/k6吞吐或真实模型性能成绩。

## 真实MySQL验收复现

本机已安装MySQL8时，可以启动全新的loopback私有实例，不使用已有MySQL服务。测试密码在脚本内随机生成，仅经子进程环境传递；测试结束后关闭该私有进程。scratch和output均须新目录：

```powershell
$mysqlBinary=(Get-Command mysqld).Source
python -X utf8 scripts/test_mysql_d1.py --mysqld $mysqlBinary --scratch "$env:TEMP/campusbridge-d1-new" --output runs/mysql-d1-new
```

测试进程使用独立迁移账号和仅DML的应用账号，并在运行前后核对源码Hash。基础环境没有MySQL时，完整pytest会明确跳过真实MySQL用例，另行运行上述入口完成真实数据库验收。

官方依据：[SQLAlchemy MySQL](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)、[InnoDB locking reads](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html)、[MySQL locking functions](https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html)、[Alembic教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)。
