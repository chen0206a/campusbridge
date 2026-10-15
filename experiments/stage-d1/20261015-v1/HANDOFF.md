# Stage D1：MySQL业务持久化验收

起点6ae84b7（Stage B已冻结且原worktree干净），分支engineering/stage-d-mysql。本轮只完成D1。主迁移、事务、配置和风险说明见[数据库文档](../../../docs/STAGE_D1_DATABASE.md)。

## 交付

- 业务存储支持MySQL8/InnoDB；SQLite继续用于轻量开发。FTS5、知识版本、source_id及Dense缓存独立存储于SQLite，不改RAG检索或Agent协议。
- Alembic d1_0001→d1_0002→d1_0003；11张业务表，新增消息位置唯一索引、工单/Run查询索引；JSON产物精确文本保存并由MySQL检查JSON_VALID。
- MySQL使用utf8mb4/utf8mb4_0900_bin、READ COMMITTED、短事务和SELECT FOR UPDATE。保留全部幂等唯一约束，并在父行锁下检查请求Hash与版本。
- ModelCall预算、usage与Trace序号在Run行锁下更新；只对1205/1213有限重试，连接故障不自动重放。
- SQLite来源只读迁移；MySQL目标须空、全量复制在一个事务内核对逐表内容Hash。知识拆分保留全部版本和来源Hash，旧库字节保持不变。
- 单Runtime保护：MySQL数据库锁专用连接、SQLite文件锁；失去所有权停止写入。重启保留工单，将中断Run/Call标为FAILED/INTERRUPTED。

## 实际验收

| 执行 | 结果 | 原始证据 |
|---|---|---|
| 完整pytest（SQLite/Mock默认环境） | 282 passed / 19 skipped / 0 failed / 0 errors；总计301条 | backend-regression/ |
| 私有真实MySQL8.0.42专项 | 37 passed / 0 skipped / 0 failed / 0 errors | mysql-exact-json-acceptance/ |
| 安装wheel后python -I验证 | Swagger200、Mock回复ACCEPTED、未导入源码树 | WHEEL_VERIFICATION.json |
| 历史与RAG保护 | 674个受保护文件零变化；Stage B原worktree校验通过 | INTEGRITY.json、STAGE_B_FREEZE.json |

19条默认skip是真实MySQL用例；专项37条含18条迁移/SQLite与19条真实MySQL测试。因此301条独立用例均有执行覆盖，未把重复执行累计为更多测试。完整pytest终端耗时202.13s，JUnit统计用例时间201.50s；MySQL专项pytest84.40s，含服务器准备总耗时87.93s。均有1条已有Starlette/httpx弃用warning。

真实数据库专项使用独立loopback MySQL进程、独立临时库和随机内存凭据，应用账号只有SELECT/INSERT/UPDATE/DELETE，迁移账号分离。没有修改系统MySQL服务；私有进程已关闭。版本、命令、源码绑定、JUnit与原始输出均保留，没有外部LLM调用。

### 并发与一致性断言

- 16线程以同一幂等键提交：1个AgentRun，内容不同或跨会话复用冲突；大小写与尾随空格不同的键独立。
- 两名辅导员16路竞争同一工单：1次认领成功、15次明确冲突，只新增1条CLAIM审计。
- 12路同一工单操作键重放：版本只增加一次，其余返回同一历史结果；正常处理与关闭仍可执行。
- 16路申请6次模型预算：只记录6次；并发重复finish不重复累计usage；32路审计序号连续且唯一。
- 12路风险持久化：1个RiskEvent、1个SupportCase、1个CREATE审计。
- API集成中12路认领得到1个200、11个409；8路重复聊天只执行1条Mock模型链，得到相同Run、2条消息与有效RAG引用。
- 应用重启标记中断调用、保留并可认领原工单；被杀死的空闲连接由pre_ping替换；连接池耗尽有限超时。
- 完整SQLite源中的已审核回复、审批、Trace、工单历史与幂等键复制后校验相等；复制后段失败时，已经写入的账号等父记录一起回滚。
- 校园、学生所有权、辅导员owner权限以及数据库唯一/JSON约束均实际验证。

线程/ASGI集成验证用于D1正确性，不是D4的网络HTTP负载实验；本轮没有RPS/P95吞吐结论。

## 关键文件

| 文件 | 改动 |
|---|---|
| src/campusbridge/db.py | 两种数据库连接、连接池、写事务、行锁、单Runtime保护与知识库类 |
| src/campusbridge/store.py | 父行/Run/工单锁，有限事务重试、唯一序号和原子幂等更新 |
| src/campusbridge/settings.py、api.py | 环境配置、业务/知识连接分离、生命周期保护 |
| src/campusbridge/schema.py、schema_v1.py、migrations/ | 冻结Schema、Alembic、结构漂移检查与显式旧库接管 |
| src/campusbridge/exact_json.py | MySQL精确JSON文本codec；SQLite保持JSON行为 |
| src/campusbridge/data_migration.py | 只读复制、知识拆分、逐表Hash、空目标与回滚检查 |
| scripts/migrate_database.py | 环境变量凭据的迁移、校验、拆分与复制入口 |
| scripts/test_mysql_d1.py | 独立真实MySQL验收、最小DML账号、凭据内存传递、源码Hash与脱敏输出 |
| tests/test_database_d1.py、test_mysql_d1.py | 新增37条测试 |
| requirements.lock、requirements-mysql.lock、pyproject.toml | Alembic及可选MySQL驱动依赖 |

## 复现

```bash
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pytest -q
python -m pip install -r requirements-mysql.lock
```

已有MySQL8二进制时，在PowerShell启动私有验收：

```powershell
$mysqlBinary=(Get-Command mysqld).Source
python -X utf8 scripts/test_mysql_d1.py --mysqld $mysqlBinary --scratch "$env:TEMP/campusbridge-d1-new" --output runs/mysql-d1-new
```

MySQL空库迁移与应用启动：使用安全进程环境提供CAMPUSBRIDGE_MIGRATION_DATABASE_URL、CAMPUSBRIDGE_DATABASE_URL及CAMPUSBRIDGE_KNOWLEDGE_DATABASE_PATH，再运行：

```bash
python scripts/migrate_database.py upgrade
python scripts/migrate_database.py verify
python scripts/serve_rag.py --port 8014
```

完整旧库迁移步骤、连接池参数及备份要求见docs/STAGE_D1_DATABASE.md。历史RAG packet的源码绑定在原Stage B worktree校验，不重写旧绑定。

## 剩余边界与停止点

支持单实例多请求并发。jobs、conversation_locks仍在进程内，没有分布式任务认领、conversation lease/fencing、自动接管或多节点知识发布；同配置第二个Runtime会被拒绝。MySQL DDL需备份与维护窗口，远程TLS/秘密管理及容器入口仍需部署阶段验收。

没有实现Redis、Compose、Locust/k6，没有新增Agent或改动Prompt，没有训练或真实模型请求。D1完成后停止，待验收后再决定D2。

## 简历可用成果

- 为校园多Agent系统实现MySQL8＋Alembic持久化与独立知识索引，保留审核后SSE、来源Hash和人工工单流程。
- 通过行锁、版本校验与幂等唯一约束，实测16路工单竞争仅1次成功；并发重复请求只生成一个业务Run。
- 建立真实MySQL迁移/恢复验收，覆盖完整审批与Trace精确复制、事务回滚和重启工单保留；301条独立测试完成覆盖。
