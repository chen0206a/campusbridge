# Stage D4：单实例 HTTP 并发与恢复验收

基于 D3 本地整合提交 `8f16609`。既有范围来自 [D1 数据库说明](STAGE_D1_DATABASE.md) 中的负载/恢复边界及 [D3 交接](../experiments/stage-d3/20261015-v1/HANDOFF.md) 的 D4 清单：使用 Mock、固定资源与镜像，验证 10/30/50/100 用户下的 HTTP、MySQL/Redis、工单竞争、幂等和恢复。跨进程任务队列、自动接管和多个 backend 不在该清单内。

## 实现与工作负载

- `deploy/compose.load.yaml` 仅用于独立 `cb-d4-*` 项目，覆盖 Mock、合成负载限流额度、只读测试脚本挂载及 MySQL 慢查询统计。默认 Compose 的限流与部署设置保持原样。
- `scripts/d4_fixture.py` 显式建立 100 个虚构学生、同校园三个辅导员及另一校园辅导员。数据库已有该批账户时拒绝再次播种。必须有专用知识卷 marker 且 Provider 为 Mock。没有从真实开发数据库导入数据。
- `scripts/locustfile_d4.py` 使用真实 HTTP：每个 VU 独立登录、建立会话，循环执行会话列表、RAG 消息、幂等重放、会话详情、来源详情与 POST SSE。VU 拥有不同账号，token 仅存在内存。
- 每条已交付回复检查 SHA-256，SSE 必须有 done 且没有 error，幂等重放检查完整响应相同，来源检查 source_id/source_hash。工单与越权竞争在独立 correctness 阶段验证。
- 逐请求记录名称、状态、耗时、字节数、异常类型，不记录 Authorization、登录 token 或密码。基准结果保留失败和超时，不筛除慢请求。

## 预先固定的验收口径

开始构建与业务请求前保存 `protocol.json` 和源码 Hash。默认每档 45 秒，10 VU/s，30 秒停止等待；思考时间 0.1–0.3 秒，请求超时 30 秒。每档独立 Locust 进程；数据库与缓存持续保留，属于渐进暖负载，不是四份同样初始状态的数据。

- 请求 RPS 和 P50/P95 包括 ramp、登录/会话建立与 graceful drain；各端点指标保留在 Locust CSV。完成的聊天请求包含幂等重放，报告明确区分请求吞吐和新 AgentRun 数量。
- 任一档错误率超过 5% 或 P95 超过 20 秒，停止升压并记录容量限制；正确性异常立即失败。它们是本次保护阈值，不是生产 SLA。
- 冷/暖缓存用首次同一查询的两个真实聊天请求记录，并结合进程 miss/hit 计数；模型、数据库与检索的整轮延迟一起测量，不能把这两次请求当作独立检索微基准。
- 独立 16 路同键消息必须仅一个 Run/助手消息；16 路同工单认领必须一项成功、其余明确冲突；错误 owner/角色/校园/学生身份不能读写他人数据。
- Redis 停止时成本敏感消息 503，数据库不得新增 Run；重启后恢复。已有 D2/D3 原子限流和 TTL 测试继续回归。
- 控制夹具持久化一个未完成 Run、STARTED ModelCall、RiskEvent 和工单，再重启 backend，要求 Run 为 FAILED/SERVER_RESTARTED、Call 为 INTERRUPTED、工单保留。该夹具没有真实模型调用；它验证持久状态的启动恢复，不等同于模拟物理断电。

## 可观测性

容器专用 `GET /ops/metrics` 只接受真实 socket loopback，Nginx 不代理该路径，伪造转发头无效。输出 SQLAlchemy pool size/checked-in/checked-out/overflow、进程 jobs/锁数量、Redis 缓存/限流计数和内存，无用户输入与凭据。通过 `docker compose exec` 在容器内读取。

每约两秒加采集耗时记录 Docker CPU/内存、MySQL 连接/行锁等待/慢查询计数。MySQL 慢阈值为 100ms，慢 SQL 原文只在专用数据库内，不导出。指标采样本身有成本，采样耗时随原始结果保留。采样池占用峰值是离散观察值，不是精确全时峰值。

负载生成器与 Docker Desktop 位于同一工作站，互相竞争 CPU/内存；100 VU 是并发客户端数，不能换算成生产并发承诺。默认单 worker，Runtime jobs 与会话锁仍在进程内。

## 复现

先安装项目 mysql/redis/test 依赖，另建负载环境安装 `requirements-load.lock`，不将 Locust 加入生产镜像。

```bash
python -m venv /external/load-venv
/external/load-venv/bin/python -m pip install -r requirements-load.lock
python scripts/run_load_d4.py --root . --output /new/external/results --project cb-d4-new --load-python /external/load-venv/bin/python --port 8081 --duration 45
```

Windows 对应使用虚拟环境 `Scripts/python.exe`。若 Docker 不在 PATH，可传 `--docker /path/to/docker`。输出目录和项目必须是全新的；脚本固定 Mock 与空 API key，拒绝远程 HTTP 目标。测试后保留所有卷，停止用相同 compose files/project 执行普通 `down`，不加 `-v`。

Windows 回归若没有兼容的 Redis server，可将 `CAMPUSBRIDGE_TEST_REDIS_COMMAND` 设置为 JSON argv `["python", "scripts/redis_docker_test_server.py"]`，并通过 `CAMPUSBRIDGE_TEST_DOCKER` 指定 Docker。该夹具只创建新的无数据卷 Redis 容器，端口绑定 loopback；凭据经 stdin 传递，测试通过原有 Redis SHUTDOWN 清理自己创建的进程，不操作部署服务。

```bash
docker compose -p cb-d4-new -f compose.yaml -f deploy/compose.load.yaml ps --all
docker compose -p cb-d4-new -f compose.yaml -f deploy/compose.load.yaml down
```

最终实测数值、失败记录和复现绑定单独写入 `experiments/stage-d4/`，不修改 D3 或之前实验结果。Locust headless、run-time 和 stop-timeout 行为依据 [官方文档](https://docs.locust.io/en/stable/running-without-web-ui.html)。
