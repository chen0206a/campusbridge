# Stage D3：单实例 Docker Compose

基线 D2 `15bdbe6`。默认 BM25、MockModel，公开的虚构演示账号沿用现有项目。Docker Desktop 使用 Linux containers / WSL2 后端。

## 一键启动

```bash
docker compose up --build -d
docker compose ps --all
```

浏览器打开 `http://127.0.0.1:8080`，Swagger 为同地址 `/docs`。学生 `student_lan` / `student_he`，辅导员 `counselor_yu`，演示密码 `CampusDemo-2026!`。这是本地作品演示配置，Web 入口仅绑定回环地址。

无需创建 `.env`、填写数据库密码或提供 LLM key。若需覆盖端口等设置，可将 `.env.example` 复制为本地 `.env`，只填写需要覆盖的字段。真实密钥不进入 Git。

## 服务与依赖

| 服务 | 职责 | 启动前提 | 持久数据 |
|---|---|---|---|
| bootstrap | 生成或复用四个独立服务密码 | 无网络 | 分角色 credential volumes |
| mysql | MySQL 8.4 LTS 业务库，utf8mb4/0900_bin | bootstrap 成功 | mysql_data |
| redis | 候选缓存和 TTL 限流，AOF | bootstrap 成功 | redis_data |
| migrate | 唯一 Alembic 迁移任务 | MySQL healthy | 只挂载迁移用户凭据 |
| backend | 单 worker FastAPI、Agent Runtime、FTS5 | migrate 成功、Redis healthy | knowledge_data |
| frontend | Node 生产构建、非 root Nginx 静态服务和 SSE 代理 | backend healthy | 无业务持久数据 |

基础镜像标签在 Compose/Dockerfile 中：`mysql:8.4`、`redis:7.2-alpine`、`python:3.12-slim-bookworm`、`node:22-alpine`、`nginxinc/nginx-unprivileged:stable-alpine`。实际版本、镜像 ID、摘要由验收保存。标签允许上游安全更新；固定复现应使用验收记录的 digest。D1 原 MySQL 8.0.42 验收记录保持原样。

依赖使用 healthcheck 和 `service_completed_successfully`，没有固定启动 sleep。backend readiness 检查 Runtime 所有权、MySQL 连接、知识库 quick_check 和必需的 Redis 限流服务。仅 frontend 暴露本机 8080，数据库/Redis/backend 不映射宿主端口；data 网络 internal，frontend 不加入 data 网络。

## 迁移、账户与权限

bootstrap 以 UID 10001 运行，密码只写入 Docker 私有 named volumes；文件原子替换并锁定，重复启动不轮换密码。四个卷分别保存 root、app、migrator、Redis 凭据。backend 只看到 app 和 Redis，migrate 只看到 migrator。密码不在 Compose environment、命令参数或源码中。

MySQL 初次空卷初始化建立业务库、DML-only `campus_app` 和 schema-scoped `campus_migrator`。运行中的 backend 不拥有 DDL 权限。迁移任务取得 D1 同一个 MySQL advisory lock，避免与另一迁移或正在运行的 Runtime 并发执行。迁移失败时 backend 不会启动。

首次部署直接 up。升级代码或迁移时先停 backend，保留数据卷：

```bash
docker compose stop backend frontend
docker compose up --build -d
docker compose exec -T backend python -m campusbridge.deployment verify
docker compose exec -T backend python -m campusbridge.deployment diagnose
```

后端 UID 10001、Redis 镜像 redis 用户、MySQL 镜像 mysql 用户、Nginx UID 101。只读根文件系统用于 backend、migrate、frontend；业务和索引目录通过专用卷写入。新卷从镜像预建目录继承所有权。capabilities/no-new-privileges 对服务按需收紧。

## 代理与身份边界

Uvicorn 禁用自动 ProxyHeaders 改写，只信任配置的 `frontend` Docker DNS 地址作为额外传输入口。默认本地开发继续只接收 loopback；不凭 X-Forwarded-For 放开访问。

Nginx 覆盖 X-Real-IP 和 X-Forwarded-For，关闭请求/响应缓冲、缓存，保持 HTTP/1.1，SSE 超时 120s。登录 peer 限流只从已验证代理读取 X-Real-IP；账户、校园和角色继续由数据库 token 认证。DNS 故障和非法转发值失败关闭。后端 IP 变化后 Nginx 使用 Docker DNS 定期重新解析。

## Redis 重启语义

同一 Redis 同时保存缓存和安全限流，因此选择 **128MiB + noeviction + AOF everysec**。缓存有 TTL；不使用可能淘汰活跃限流键的 allkeys-lru。内存耗尽/Redis 不可用时缓存旁路、限流固定 503，沿用 D2 策略。监控持续 OOM 时应扩容或分离缓存和限流实例。

正常 restart/down 会刷新 AOF，未过期窗口和缓存可恢复，TTL 仍按绝对时间推进。突然断电最多可能丢失约一秒 AOF；已经过期的限流窗口不会复活。密码卷与 Redis 数据卷必须成套保留。正式高可用限流和跨节点容灾另行设计。

## SQLite RAG 持久化

FTS5、不可变文档版本、Embedding 内容缓存一起放在 `knowledge_data` 的 index.sqlite3。启动只导入尚不存在的内置文档，不会把已更新的旧文档重新激活。MySQL 不承担 FTS5 SQL，业务库与知识索引保持 D1 解耦。

新版本导入示例：

```bash
docker compose cp ./new-knowledge.json backend:/tmp/new-knowledge.json
docker compose exec -T backend python -m campusbridge.deployment import-knowledge --file /tmp/new-knowledge.json
```

输入为原 DocumentInput 数组，需提供 document_id、campus_id、title、revision、license、author、origin、text。同 revision 内容不可修改；新 revision 激活时旧版失效。D2 缓存键绑定完整目录指纹，更新后的旧缓存不会作为当前证据。历史来源仍可查询 active_revision=false，当前候选与引用继续校验校园和实际来源 Hash。

`diagnose` 返回安全的 Schema/行数、知识版本/Hash、SQLite quick_check、Redis AOF 和缓存条目统计；不返回密码、token、对话内容。验收还核对真实 HTTP 来源内容和引用 Hash，以及 MySQL/SQLite 在重启后的持久身份。

## 可选 Dense / Hybrid

BM25 镜像不安装 Torch，不下载任何模型。已有 BGE-small-zh-v1.5 文件时，设置本地目录与模式，选择独立镜像标签：

```bash
# 通过本机环境设置 CAMPUSBRIDGE_BGE_MODEL_DIR、CAMPUSBRIDGE_RETRIEVAL_MODE=hybrid
# 可设置 CAMPUSBRIDGE_IMAGE_TAG=d3-dense 避免与 BM25 本地镜像混淆
docker compose -f compose.yaml -f deploy/compose.dense.yaml up --build -d
```

Dense target 安装 requirements-rag.lock，模型只读挂载到 `/models/bge-small-zh-v1.5`。预热在开始接收请求前执行，缺失模型默认报错，可显式设置 retrieval fallback=bm25。语义向量内容缓存仍随知识卷持久化，原始模型权重不进入镜像/Git。

Stage D2 本地 CPU 观测：模型及依赖首次加载约 18.77s、8 块索引约 0.65s，加载前后 RSS 差约 248MiB、峰值约 383MiB；这是 Windows 的历史实测，不是 D3 容器性能。Docker Desktop 建议至少 4 CPU / 6GiB 可用内存，Dense 留出约 2GiB 额外余量和依赖镜像磁盘空间。首次构建会下载 CPU 推理依赖，仍不下载模型权重。

## 日常操作和排错

```bash
docker compose logs --tail=100 backend migrate mysql redis frontend
docker compose restart backend redis
docker compose down
docker compose up -d
```

普通 down 保留卷。**`docker compose down -v` 会删除数据库、知识索引、Redis 及密码卷**，仅用于明确决定清空演示数据时；本轮验收不执行它。备份/恢复时将 MySQL、knowledge、Redis 和 credential volumes 作为一套管理。

镜像拉取失败先检查 Docker Desktop 的代理配置；宿主 shell 的 HTTP_PROXY 不等于 Desktop 已使用它。新安装后旧终端可能缺少 Docker credential helper 的 PATH，重开终端或将 Docker resources/bin 加入当前进程 PATH。构建网络需要代理时使用本机 Docker 的代理设置，不把个人代理写进公开 Compose。

## 验收与 D4 准备

```bash
python scripts/compose_acceptance.py --root /clean/source --project cb-d3-acceptance --output /new/results
python scripts/compose_redis_probe.py --root /clean/source --project cb-d3-acceptance --output /new/redis-probe.json
```

脚本只操作专用 cb-d3-* 项目，以 MockModel 运行真实 HTTP；记录首次 build→healthy、backend/Redis restart 和 down→up 的耗时，检查来源、幂等、权限、Trace、工单关闭、版本失效和持久数据。缺少 Engine 明确 BLOCKED，执行失败明确 FAILED，未执行步骤不计通过。浏览器演示单独记录。

Runtime 仍单 worker，jobs/conversation_locks 在进程内；D1 Runtime lock 继续生效。D4 前需要固定镜像 digest、机器资源、Mock 工作负载、限流阈值与预热方式，记录 MySQL/Redis 观测，并继续单实例测试。多实例任务认领与恢复属于独立改造，本阶段不配置 scale backend=2。

官方依据：[Compose 启动依赖](https://docs.docker.com/compose/how-tos/startup-order/)、[MySQL Official Image](https://hub.docker.com/_/mysql)、[Redis Official Image](https://hub.docker.com/_/redis)、[Nginx unprivileged image](https://github.com/nginx/docker-nginx-unprivileged)。

## RC 宿主环境排错补充

Windows npm 若共享缓存不可写，使用本次安装的独立目录：`npm ci --cache ./tmp/npm-cache`（在 frontend 下执行；tmp 已忽略），不需要改锁文件或升级依赖。Windows MySQL 8.0 私有测试二进制若截断中文 scratch 路径，给 `scripts/test_mysql_d1.py --scratch` 指定新的 ASCII 临时路径；应用及 Compose 支持原中文工程路径，Docker volume 无此宿主二进制路径限制。

Linux 对已安装包执行测试时，若测试需要 eval 或源文件 Hash，应把完整克隆目录只读挂载，而非仅挂载 tests；用 `pytest -o pythonpath=` 确认导入镜像内安装包。RC 实际命令和失败首记录保存在验收资料中。
