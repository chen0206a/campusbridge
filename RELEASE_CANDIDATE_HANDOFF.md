# CampusBridge Release Candidate 验收

**本地 RC 验收通过，可以在用户确认后发布。** 本轮没有推送 GitHub、创建远程仓库、Release 或 tag。

## 提交与范围

D5 `d583531d6a5178537cbd0a38959195046f378058` 已完整合并本地 main，合并 SHA **`d81cc0aa7216e2704669bfbf0cc9d9f532e18f62`**。RC 整理分支为 `release/candidate-20261016`，最终归档提交同步本地 main，完整历史保留。

独立验收克隆的源码 SHA 为 **`974bd0898324893e5469f3bb60ee1de40adc03ed`**。此后只补充文档、CI source-audit、发布扫描/导出防护和证据，Agent、Prompt、事务、检索与前端运行源码未变。**最终发布 SHA 用 `git rev-parse main` 读取**，与交付消息和工程外 `FINAL_RC.json` 一致；本文件本身随最后归档提交版本化，将源码验收与文档归档提交分开记录。

## 新克隆与测试（2026-10-16）

实际使用 `git clone --no-local --branch release/candidate-20261016` 克隆本地仓库到新目录。没有复制开发数据库、venv、node_modules 或 `.env`。新环境为 CPython 3.12.15、Windows Node 24.15.0/npm 11.12.1；Docker 前端用 Node 22 构建。

| 检查 | 实际结果 |
|---|---|
| 全新安装 | requirements.lock、editable install、pip check 通过；先仅基础依赖跑通 Mock 和 HTTP，再安装可选 MySQL/Redis 锁文件 |
| 完整后端 | **355 passed / 21 opt-in skipped / 0 failures / 0 errors**，224.22s |
| 真实 MySQL/Redis | **39 passed**，78.40s；覆盖全部 21 项 opt-in，另 18 项重叠，合计 **376 个独立后端用例通过** |
| Linux 已安装包 | **116 passed**，48.84s；完整克隆只读挂载，清空 pytest pythonpath，从镜像 site-packages 导入应用 |
| 前端 | **27 passed**，TypeScript 和 Vite 构建通过，独立缓存 npm ci 报告零漏洞 |
| Mock 演示 | ordinary / rag / human / risk 均 ACCEPTED；工单 OPEN→CLAIMED→IN_PROGRESS→CLOSED |
| HTTP | Swagger/OpenAPI、登录、RAG 引用、真实 SSE、工单、权限及幂等通过 |
| 镜像绑定 | 安装包 **45 个** Python/JSON 文件与克隆源码 SHA-256 一致 |

[机器可读验收与原始日志索引](docs/release-candidate/20261016-v1/ACCEPTANCE.json)。继承的 Starlette/httpx 弃用提示保留。

## Docker 与持久化

专用项目 `cb-d3-rc-20261016`，http://127.0.0.1:8085/；默认 Mock + dynamic + BM25，0 外部模型调用。MySQL 8.4、Redis、单 Backend、前端均 healthy，bootstrap/migrate 正常退出，Alembic `d1_0003` 验证通过。

新空卷完成学生登录、知识引用及来源 Hash、SSE done、辅导员工单认领/处理/关闭、身份隔离、幂等和 Trace。知识版本更新后旧来源失效，历史来源仍可查询。backend/Redis restart、保留卷的 down/up 后，MySQL 行数、已关闭工单、对话、知识身份和 Redis 标记一致。

| 操作 | 秒 |
|---|---:|
| 新克隆/空卷构建到健康（已有基础镜像与构建缓存） | 44.29 |
| backend + Redis 重启 | 7.24 |
| down 保留卷，再 up 到健康 | 24.49 |

额外 Redis 探针：暖缓存 GET +1/SET +0，引用一致；重启后限流 count=1、TTL 600→598，剩余两次允许，下一次 429。原有 Docker 项目、数据库与所有原数据卷保留。[Compose 实录](docs/release-candidate/20261016-v1/compose/result.json)

## 公开文件、历史与许可

- 当前跟踪文件和 `git rev-list --objects --all` 全部可达历史扫描通过。覆盖删除过的版本及 commit/tag 消息；报告只记录路径、规则、Object ID。五个精确的离线/测试凭据字面值有说明和测试，未按文件整体排除。PNG 元数据独立检查。
- 源码命名空间、角色 Prompt、知识/合成用例与来源记录一致；仓库文件和历史未发现无关工程源码/素材包。14 项 showcase 素材 Hash、来源与许可一致，原图保留，当前架构图另存。
- MIT LICENSE 正文完整。当前 **33 项基础/MySQL/Redis、21 项可选 RAG、41 项压测工具、212 条 npm** 记录的版本/许可元数据齐全，pin 无差异；集合有重叠。上游依赖、镜像与可选模型保留各自许可和署名。发布导出与 Git 忽略规则同步排除嵌套模型/临时缓存和证书，额外 16 项发布门禁测试通过。
- `.gitignore` 补充虚拟环境、模型缓存、Docker 数据、临时目录及证书格式。CI 增加完整 fetch 的 source-audit；当前 RC 的远程 CI 等确认推送后执行。
- 相对 D5 提交的 **1,021 个**实验/eval/Prompt/知识/原素材文件逐字节一致；历史 archive verifier **560 项**通过。Stage 1–6、RAG A/B、D1–D5 记录完整保留。

[来源](docs/PROVENANCE.md) · [素材](docs/ASSETS.md) · [第三方](THIRD_PARTY_NOTICES.md) · [依赖盘点](docs/release-candidate/20261016-v1/dependency-inventory.json) · [发布清单](docs/RELEASE_CHECKLIST.md)。本次来源审阅结合历史与自动扫描，模式匹配本身不作为作者身份的法律证明。

## 交付位置

| 材料 | 位置 |
|---|---|
| 中英文入口、安装与部署 | [README](README.md)、[English](README.en.md)、[Compose](docs/STAGE_D3_COMPOSE.md) |
| 架构与执行计划 | [架构说明](docs/ARCHITECTURE.md)、[ExecutionPlan/Trace](docs/EXECUTION_EXAMPLES.md) |
| 当前清晰架构图 | [PNG](docs/assets/campusbridge-release.png)、[SVG](docs/assets/campusbridge-release.svg)、[drawio](docs/assets/campusbridge-release.drawio) |
| 学生/辅导员与工单截图 | [展示索引](docs/PROJECT_SHOWCASE.md)、[辅导员无损 PNG](docs/assets/showcase/counselor-workbench.png) |
| DeepSeek 真实实验 | [Stage 4–6](docs/EXPERIMENTS.md)、[原始历史](experiments/README.md) |
| Locust 与瓶颈证据 | [D4](experiments/stage-d4/20261015-v1/HANDOFF.md)、[D5](experiments/stage-d5/20261016-v1/HANDOFF.md) |
| 仓库描述、Topics、推荐结构 | [GITHUB_METADATA](docs/GITHUB_METADATA.md) |
| Release Notes / 2–3 分钟脚本 | [发布说明](docs/RELEASE_NOTES_RC.md)、[演示脚本](docs/DEMO_SCRIPT.md) |
| RC 实际命令与测试证据 | [复现命令](docs/release-candidate/20261016-v1/REPRODUCE.md)、[ACCEPTANCE](docs/release-candidate/20261016-v1/ACCEPTANCE.json) |

README 展示 Stage 6 的 28/30、边界 10/12→12/12、任务 176→150、P50 4.32→3.43s、请求 125→126；D5 同日 100 VU 对照 RPS +20.26%、P95 −15.09%、P99 −17.01%。本轮直接保留这些原成绩，没有新模型或性能实验。

## 问题记录与状态

三项环境问题已保留首记录并复验：共享 npm 缓存 EPERM→独立缓存；Windows MySQL 8.0 中文 scratch 路径截断→ASCII scratch；首次 Linux 仅挂载 tests/scripts 缺 eval→完整只读克隆挂载。没有修改业务用例或放宽测试。

**本地公开发布阻塞项：无。** 等待用户确认推送和远程 CI。默认回环 Web 入口和公开虚构演示账号用于本地体验；运行范围为单 Backend，100 VU 是短时 Mock HTTP 负载，尾延迟及容量边界保留在 D5 报告。

验收副本仅脱敏工作站路径、终端转义并规范报告格式，RAW_PROVENANCE 保留原始/副本 SHA。原始日志、新克隆、净源码导出和最终 SHA 记录留在工程外 RC 输出目录；发布包只包含允许清单内容。
