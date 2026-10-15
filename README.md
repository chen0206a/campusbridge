# CampusBridge

[![CampusBridge CI](https://github.com/chen0206a/CampusBridge-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/chen0206a/CampusBridge-Agent/actions/workflows/ci.yml)

**原创校园学生支持多 Agent Web 应用：动态任务规划、证据仲裁、审核后交付与人工跟进闭环。**

An original campus-support application with typed task planning, concurrent agent analysis, evidence arbitration, reviewed delivery, and auditable human follow-up. [English README](README.en.md)

Python · FastAPI · Pydantic · SQLAlchemy · SQLite FTS5/BM25 · React · TypeScript · Vite · MIT

[快速运行](#快速运行无需-api-key) · [架构](#动态协作架构) · [真实实验](#真实-deepseek-实验) · [演示脚本](docs/DEMO_SCRIPT.md) · [复现记录](docs/CLEAN_REPRODUCTION.md)

## 产品与技术特点

- **动态协作**：TaskPlanner 构建类型化依赖图；输入独立时 Intent/Risk 并行，历史指代保留串行依赖；按最终路由运行或跳过 ContextAgent。
- **结果仲裁**：Arbitrator 综合结构化输出、服务端风险规则与检索证据，记录冲突、采用的证据 Hash、最终路由和决策原因。
- **生成后审核**：ResponseAgent 生成完整候选；OutputReviewer 审核实际文本并绑定候选 ID、正文与引用 Hash。通过后才进入 SSE；最多一次重生成，随后固定安全兜底。
- **可信引用**：原创校园资料版本化导入、FTS5/BM25 检索；只允许本轮有效来源，引用可查看原文、版本和许可。
- **人工闭环**：需要人工关注时先持久化 RiskEvent/SupportCase，生成失败仍保留事项；OPEN → CLAIMED → IN_PROGRESS → CLOSED，操作有权限、版本、幂等与审计约束。
- **完整产品**：学生端登录、历史、多轮、POST/fetch SSE、刷新与断连恢复；辅导员工单筛选、认领、处理、关闭和操作历史。
- **可复现评测**：冻结数据/Prompt/源码绑定、原始模型输出与 Trace、逐条计分及 SHA-256；Mock 回归与自然/受控故障分别统计。

## 界面展示

学生对话与可追溯知识引用：

![Student workspace](docs/assets/showcase/student-chat.jpg)

辅导员工作台与完整处理记录：

![Counselor workspace](docs/assets/showcase/counselor-workbench.png)

[来源详情、移动端与 OPEN/CLAIMED/IN_PROGRESS/CLOSED 全流程截图](docs/PROJECT_SHOWCASE.md)。全部演示账号、表达和工单为虚构数据。

## 动态协作架构

![CampusBridge architecture](docs/assets/campusbridge-stage6.png)

TaskPlanner、TaskScheduler 与 Arbitrator 使用确定性代码策略，已有 Agent 通过统一 ModelPort 返回独立类型化结果，协调器串行合并业务状态。每次数据库操作使用短 Session，模型 await 期间不共享 SQLAlchemy Session。

| 场景 | 执行路径 |
|---|---|
| 普通聊天 | Intent ∥ Risk → 仲裁 → 跳过检索、保留历史 → Response → Review → Delivery |
| 校园知识 | Intent ∥ Risk → 仲裁 → Context/RAG → 证据仲裁 → Response → Review → Delivery |
| LOW 人工需求 | 分离风险严重程度与人工需求 → 提交支持事项 → 回复审核交付 |
| 高风险与知识诉求并存 | 先提交风险检查点 → 仲裁优先人工支持 → 跳过知识任务 → 回复审核交付 |

任务配额、模型调用、保守 token 预留、超时与取消分别受控。Trace 记录开始/结束区间、依赖、跳过原因、仲裁证据、候选与审批绑定；学生视图隐藏内部风险和候选调试信息。

[Sequence / ExecutionPlan / Trace 示例](docs/EXECUTION_EXAMPLES.md) · [详细编排协议](docs/STAGE6_ORCHESTRATION.md) · [可编辑架构图](docs/assets/campusbridge-stage6.drawio)

## 快速运行（无需 API key）

需要 Python 3.12+、Node.js 22.12+。先克隆仓库，再在工程根目录执行：

```bash
git clone https://github.com/chen0206a/CampusBridge-Agent.git
cd CampusBridge-Agent
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
Push-Location frontend
npm ci
npm run build
Pop-Location
$env:CAMPUSBRIDGE_MODEL_PROVIDER='mock'
$env:CAMPUSBRIDGE_ORCHESTRATION='dynamic'
.\.venv\Scripts\python.exe scripts/web_backend.py --port 8015
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
(cd frontend && npm ci && npm run build)
CAMPUSBRIDGE_MODEL_PROVIDER=mock CAMPUSBRIDGE_ORCHESTRATION=dynamic \
  .venv/bin/python scripts/web_backend.py --port 8015
```

应用：http://127.0.0.1:8015/；Swagger：http://127.0.0.1:8015/docs。构建后由同一后端提供前端和 API。开发模式可在 `frontend/` 运行 `npm run dev`，Vite 通过相对 `/api` 和可配置代理连接后端。

| 演示账号 | 角色 |
|---|---|
| student_lan / student_he | 两个虚构学生，用于验证会话隔离 |
| counselor_yu | 同校园辅导员 |

演示密码均为 `CampusDemo-2026!`。MockModel 默认可用，不需要外部服务或模型下载。`.env.example` 列出配置，应用读取进程环境变量，不会自动加载该文件。

[前端开发说明](frontend/README.md) · [API 与认证](docs/API.md) · [2–3 分钟演示脚本](docs/DEMO_SCRIPT.md)

## 真实 DeepSeek 实验

固定30条包含20条自然案例和10条受控故障，边界集12条单独统计。历史结果保留原始评分。

| 阶段 | 固定30 | 自然20 | 受控故障10 | 边界12 |
|---|---:|---:|---:|---:|
| Stage 4 | 26/30 | 17/20 | 9/10 | — |
| Stage 5 | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage 6 Sequential | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage 6 Dynamic | 28/30 | 18/20 | 10/10 | **12/12** |

Stage 6 成对对照保持角色 Prompt、模型、原始案例/gold、生成参数与评分检查相同：thinking disabled、temperature 0、最大输出1024 tokens。

| 指标 | Sequential | Dynamic |
|---|---:|---:|
| 固定集成功 | 28/30 | 28/30 |
| 边界集成功 | 10/12 | **12/12** |
| 固定集 Agent 任务执行 | 176 | **150** |
| 固定集 P50 | 4.32s | **3.43s** |
| 固定集模型请求 | 125 | 126 |
| 边界集兜底 | 2 | **0** |

两个历史风险案例的 gold 为 MODERATE，当前输出 LOW + needs_human_support=true，仍正确进入人工流程，保留为 risk 字段失败。Dynamic 改善无关知识命中与用户伪造来源标记；一次漏引用经重生成恢复，使固定集模型请求增加1。对照衡量动态策略整体，单次API延迟记录与模型请求数分别展示。

[完整实验、费用和失败分析](docs/EXPERIMENTS.md) · [逐条历史结果与 Hash 索引](experiments/README.md)

## 测试与离线复现

使用当前 Python 环境：

```bash
python -m pytest -q
python scripts/showcase_demo.py --output runs/demo-001
python scripts/http_smoke.py --output runs/http-001.json
python scripts/verify_archive.py
(cd frontend && npm test && npm run build)
```

`showcase_demo` 保存四种动态路径和完整工单状态；`http_smoke` 启动独立临时服务，检查 Swagger、SSE 和工单闭环后停止自己的服务。测试和演示使用隔离数据库与合成数据。

发布准备已从干净目录验证 **后端203项、前端27项通过**，前端生产构建与HTTP演示通过。

GitHub Actions 配置覆盖 Ubuntu/Windows、Python 3.12/3.13 后端，以及 Node 22 前端测试与构建。CI 不配置真实模型密钥。[干净目录复现报告](docs/CLEAN_REPRODUCTION.md)

## RAG 专项检索基线

[Stage A检索评测](docs/RAG_STAGE_A.md)：72条固定合成开发查询、16篇隔离语料。当前BM25 Chunk Recall@1/3/5为60.71%/76.79%/76.79%，MRR为0.7143。逐条结果、配置与Hash独立保存，0模型调用，生产检索方案保持不变。本轮后端回归228项通过。

## RAG Dense / Hybrid

[Stage B实现与复现](docs/RAG_STAGE_B.md) · [三模式完整结果](experiments/rag/stage-b/20261015-v1/analysis/COMPARISON.md)。原72条BM25 / Dense / Hybrid Recall@3为 **76.79% / 91.07% / 89.29%**；预先冻结的32条Holdout为 **83.33% / 85.42% / 89.58%**。默认BM25，安装本地CPU Embedding后可通过环境变量切换，继续使用原引用和审核流程。

## 业务数据库

Stage D1新增可选MySQL8业务库与Alembic迁移，知识检索保留独立SQLite，支持工单行锁、持久幂等约束和单Runtime启动保护。[迁移、配置与风险清单](docs/STAGE_D1_DATABASE.md) · [D1验收报告](experiments/stage-d1/20261015-v1/HANDOFF.md)。本地默认仍用SQLite＋MockModel。

## Redis 检索缓存与限流

Stage D2 提供可独立启用的 BM25 / Dense / Hybrid 候选缓存，以及登录、消息提交的原子限流。缓存命中后重新验证校园、有效文档版本和来源 Hash；Redis 故障时检索回源，认证与消息限流返回明确的 503。默认关闭 Redis，继续支持 SQLite＋MockModel 本地体验。

[配置与一致性设计](docs/STAGE_D2_REDIS.md) · [真实 Redis 验收与性能结果](experiments/stage-d2/20261015-v1/HANDOFF.md)。Runtime 保持 D1 的单实例运行约束。

## Docker Compose 本地部署

```bash
docker compose up --build -d
```

打开 `http://127.0.0.1:8080`，无需模型 API Key 即可使用 MockModel。Compose 管理 MySQL 8、Redis、单实例后端和生产前端，独立任务负责凭据初始化与 Alembic 迁移；知识 SQLite 和服务数据使用持久卷。

[部署、迁移、Dense 可选配置与排错](docs/STAGE_D3_COMPOSE.md) · [D3 实际验收](experiments/stage-d3/20261015-v1/HANDOFF.md)

## 可选真实模型

从服务端进程环境传入 `CAMPUSBRIDGE_MODEL_PROVIDER=openai-compatible`、`CAMPUSBRIDGE_ENDPOINT`、`CAMPUSBRIDGE_MODEL`、`CAMPUSBRIDGE_API_KEY`；DeepSeek 使用 `CAMPUSBRIDGE_THINKING_MODE=disabled`。密钥只放服务端环境，前端没有模型密钥。

原始历史包用于查阅。新复现先生成绑定当前源码的新 packet，再运行评测，并指定费用上限，详见 [实验命令](docs/EXPERIMENTS.md)。

## 工程结构

```text
src/campusbridge/   Agent、协议、规划/仲裁、Runtime、API、事务、检索
frontend/          React/Vite/TypeScript 学生与辅导员界面
scripts/           本地服务、演示、固定评测与公开包检查
tests/            单元、并发、API、端到端和发布边界测试
eval/              原创冻结合成案例
experiments/       原始历史结果的公开副本、Hash 与实验索引
docs/              架构、截图、演示、来源、复现与发布检查
.github/workflows/ 无外部模型的自动化检查
```

## 来源与许可证

原创源码、角色 Prompt、校园参考资料、界面图标和合成用例按 [MIT LICENSE](LICENSE) 提供；[来源说明](docs/PROVENANCE.md) 与 [第三方许可](THIRD_PARTY_NOTICES.md) 列出依赖和素材来源。系统定位为学生支持与人工跟进，心理疾病诊断和实际干预由专业人员承担。

[贡献方式](CONTRIBUTING.md) · [安全与部署边界](SECURITY.md) · [开源检查清单](docs/RELEASE_CHECKLIST.md) · [简历项目介绍](docs/PORTFOLIO.md)
