# Stage D3 完成交接

基于 D2 `15bdbe6`，分支 `engineering/stage-d-compose`。完成 Docker Desktop D 盘安装、单实例 Compose、真实 HTTP/浏览器集成验收及恢复检查。全部使用 MockModel，外部模型请求 0。

## 实现

bootstrap 生成四组独立凭据 → MySQL/Redis healthcheck → 单一 Alembic migrate → 单 worker backend → Nginx frontend。MySQL 业务库、SQLite FTS5/Embedding 缓存、Redis AOF 分卷持久化。默认 BM25，不下载 BGE；Dense/Hybrid 通过可选 CPU 构建与只读模型挂载启用。

后端与 Nginx 非 root、只读根文件系统，数据库不映射宿主端口。Nginx 关闭 SSE 缓冲并覆盖代理 IP；服务端只信任指定代理，身份/校园仍由原有数据库校验。缓存命中保留 D2 的版本/hash 验证。Agent Prompt、编排及 RAG 排名未改动。

## 实测结果

| 检查 | 结果 |
|---|---|
| 默认完整后端回归 | 330 passed / 20 opt-in skipped / 0 failed，117.91s |
| 私有真实 MySQL 8.0.42 + Redis 回归 | 38 passed，43.76s；覆盖上述全部 20 项 opt-in，另 18 项有重叠，后端合计 350 个独立测试通过 |
| Linux 容器安装包测试 | 36 passed，14.24s；43 个源码/资源文件 Hash 与工作树一致 |
| 前端 | 27 passed，6.37s；生产构建通过 |
| Compose 实际版本 | Desktop 4.95.0 / Engine 29.8.2 / Compose 5.6.0；MySQL 8.4.11、Redis 7.2.16、Nginx 1.30.6 |
| 干净应用目录、空卷启动 | 33.12s，镜像和构建层已有缓存，排除首次网络下载 |
| backend + redis 重启 | 7.27s |
| down 后保留卷再启动 | 22.84s |
| 历史数据 | 727 个既有文件 SHA-256 全部不变 |

真实 HTTP 检查覆盖登录、RAG/SSE/引用、权限、消息幂等、Trace、工单全部状态、文档版本切换与缓存失效。浏览器完成学生来源详情、刷新恢复、辅导员工单闭环。重启前后 MySQL 表记录数、SQLite 知识身份与有效版本一致；Redis marker 恢复。额外探针证实缓存 GET +1/SET +0、引用相同；Redis 重启后 3 次限流窗口的 count=1 保留、TTL 600→598，剩余两次允许、第四次 429。

## 运行与复现

```bash
docker compose up --build -d
docker compose ps --all
docker compose exec -T backend python -m campusbridge.deployment diagnose
docker compose logs --tail 100 backend
docker compose down
```

Web `http://127.0.0.1:8080`，Swagger `/docs`。当前验收项目 `cb-d3-final-v4` 保持运行，命令需在其干净源码目录使用相同 project name；通常从本仓库首次启动使用默认项目名即可。若端口 8080 已被验收环境占用，先停止该项目或通过 `.env` 改 Web 端口。

自动验收使用新的空输出目录和 `cb-d3-` project name，命令详见 [部署文档](../../../docs/STAGE_D3_COMPOSE.md)。完整命令、逐项结果、镜像版本、源码绑定、失败记录见本目录原始日志及 JSON。README 和 `CHANGED_FILES.json` 列出核心文件。

测试命令（项目根目录；前端使用 `--prefix`）：

```bash
python -X utf8 -m pytest -q
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
python scripts/test_mysql_d1.py --mysqld /path/to/mysqld --scratch /new/private-scratch --output /new/mysql-results --extra-test tests/test_redis_mysql_d2.py
docker compose run --rm --no-deps -v /absolute/project:/checks:ro -w /checks backend python -m pytest tests/test_api.py tests/test_web_api.py tests/test_deployment_d3.py -q --basetemp=/tmp/cb-tests -p no:cacheprovider -o pythonpath=.
```

MySQL 驱动启动专用临时数据库，不连接现有系统库。Redis 测试夹具要求本机 Redis 或可启动 Redis 的 WSL；依赖安装使用现有 mysql/redis 锁文件。容器测试挂载测试源码，但 `pythonpath=.` 使用镜像中安装的应用包。

## 下一阶段准备

只支持一个 Backend；jobs/conversation_locks 保持进程内。未配置 `--scale`。D4 可在独立测试项目/合成数据下验证 10/30/50/100 用户 HTTP 负载、连接池/资源监控、并发认领和幂等；本轮没有执行压测。

默认 BM25 容器链路已验收。Dense 可选容器镜像未实际加载模型；Stage B/D2 的 CPU 检索测试与历史结果保留。AOF everysec 的突然断电窗口约一秒，正常 restart 恢复已实测；未测试主机断电。当前 Mock 对含糊人工请求的用词覆盖观察见浏览器记录。

没有运行真实模型、D4、推送或合并 main。历史 Stage A/B/D1/D2 及 Stage 1–6 记录保持原样。
