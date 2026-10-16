# GitHub v1.0.0 发布元信息

仓库：`chen0206a/campusbridge`，发布远程名 `campusbridge`。原 `origin` 保留，独立仓库不改写其他项目。

英文 Description（144 字符以内）：

> Typed multi-agent campus support with dynamic planning, grounded RAG, reviewed SSE, auditable cases and MySQL/Redis Compose deployment.

中文简介：

> 校园学生支持多 Agent 应用，结合动态规划、有限并行、证据仲裁、审核后交付与人工闭环，提供 MySQL/Redis 容器部署及可追溯评测。

Topics：

```text
multi-agent fastapi react typescript rag hybrid-search llm-safety
mysql redis docker-compose observability locust student-support
```

Release 标题：`CampusBridge v1.0.0 — Reviewed agents & reliable delivery`

正文使用 [RELEASE_NOTES_v1.0.0.md](RELEASE_NOTES_v1.0.0.md)。私有阶段完成远程 CI 后切换公开，再从通过验收的 main 创建 v1.0.0；原 RC 和历史 CI 记录保留。

## 推荐仓库结构

```text
README.md / README.en.md / LICENSE / THIRD_PARTY_NOTICES.md
RELEASE_CANDIDATE_HANDOFF.md
src/campusbridge/           领域、Agent、ModelPort、调度、事务与检索
frontend/                  React/Vite/TypeScript 与锁文件
tests/                     Mock、API、并发、数据库、Redis、发布测试
scripts/                   演示、迁移、评测、诊断与发布检查
deploy/ + Dockerfile       镜像、代理与 Compose 可选配置
compose.yaml               单 Backend 的完整本地部署
requirements*.lock         基础与各可选环境依赖
eval/                      冻结合成评测输入与协议
experiments/               Stage 1–6、RAG A/B、D1–D5 原始证据
docs/                      架构、许可、截图、演示与 RC 验收证据
.github/workflows/         Mock CI，Ubuntu/Windows 和前端构建
```

venv、数据库/卷、缓存、模型、`.env`、运行日志留在 Git 外。可发布源码包由 `scripts/release.py build` 生成独立目录及 SHA-256 清单。
