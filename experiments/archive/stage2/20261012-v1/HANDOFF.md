# CampusBridge 第二阶段验收

已实现可配置 OpenAI-compatible ModelPort、版本化 SQLite FTS5/BM25 校园知识检索与固定 30 条评测。默认 MockModel 继续离线运行，原 API、最终回复审核与人工工单流程保持正常。

## 实际验证

| 检查 | 结果 |
|---|---|
| 完整 pytest | **119 passed / 0 failed / 0 errors**，91.66 秒 |
| 原 MVP 测试 | 59 项保持原文件、全部通过 |
| 本轮新增测试 | 60 项：Provider、检索/引用、API 和冻结评测 |
| 固定 Mock 评测 | **30/30**，自然 20/20，受控故障 10/10 |
| 引用来源核验 | 5/5，100% |
| Mock 评测总耗时 | 8.438 秒，140 次 ModelPort 调用（含准备轮），0 外部请求 |
| 真实 HTTP / uvicorn | Swagger/OpenAPI 200；SSE 与完整链通过 |
| 独立 wheel 安装 | 打包并用 Python -I 从独立安装目录运行，通过 |
| 人工工单 | OPEN → CLAIMED → IN_PROGRESS → CLOSED，通过 |
| 原历史保护文件 | 47/47 SHA256 不变：旧测试与 MVP 报告 |
| 冻结评测包 | 执行前后验证通过，30 条用例，21 个运行源码文件绑定 |

当前环境未配置 CAMPUSBRIDGE_ENDPOINT、CAMPUSBRIDGE_MODEL、CAMPUSBRIDGE_API_KEY，真实评测入口返回 `PENDING_REAL_CONFIGURATION`，0 请求。以上成绩是 Mock 工程行为验证；真实模型成绩待配置后记录。

pytest 有 1 条已知 Starlette TestClient/httpx 弃用提示，现有固定依赖下全部检查正常。运行依赖只将已有 httpx 纳入 ModelPort 所需依赖，未增加向量服务或模型相关依赖。

## 代码变更

| 模块 | 主要实现 |
|---|---|
| `provider.py`、`serialization.py`、`settings.py` | endpoint/model/key 配置、httpx 客户端、JSON envelope、严格角色 JSON、usage、稳定错误代码、无自动重试 |
| `prompts_v2.py`、`agents.py` | 四份原创角色 Prompt；schema 追加；查询指代；真实文本与来源审核；缺失/未知引用处理 |
| `retrieval.py`、`resources/knowledge-v2.json` | 8 份原创 MIT 资料；版本不可改写；事务化切块/FTS；中文二元词、英文词、BM25、校园隔离 |
| `contracts.py`、`policy.py` | 引用协议；candidate 文本及 citations 元数据 Hash 绑定审核 |
| `db.py`、`store.py`、`runtime.py` | 新增 ModelCall、知识文档/切块表；逐次调用持久化、失败恢复、审核后引用交付 |
| `api.py`、`models.py` | 配置切换；来源查询；Mock 引用/无命中处理；保留原工单和接口 |
| `evaluation.py`、`scripts/evaluate.py`、`eval/stage2/` | 固定 30 条、调用前 freeze、来源核验、故障控制与详细结果 |
| `scripts/import_knowledge.py`、`scripts/demo_stage2.py` | 独立导入、知识链和 Provider HTTP 故障演示 |
| 新增 4 份测试文件 | test_provider、test_retrieval、test_stage2_api、test_evaluation |
| README、.env.example、docs | 启动、模型配置、导入、冻结/评测命令及评分口径 |

SQLAlchemy 在旧 SQLite 上增建新表；旧表列与数据保持，旧报告保留自己的历史 Hash。改造前源码快照：`campusbridge-mvp-source-snapshot.zip`。

## 一条带知识引用的 Trace

学生问题：“图书馆预约座位需要做什么？”

1. IntentAgent → CAMPUS_SERVICE / needs_knowledge=true。
2. RiskAgent → LOW / 无人工关注。
3. ContextAgent → KNOWLEDGE_SUPPORT / HIT，获取 `campus-library` 的原文切块和版本。
4. ResponseAgent → 完整候选正文，使用 `[source:实际切块ID]`；代码从命中原文构建引用。
5. OutputReviewer → PASS，绑定 candidate_id、content_hash、citations_hash。
6. Delivery → ACCEPTED，提交助手消息，再发送 SSE。

4 次模型端口调用、1 条有效引用、0 工单。完整证据：`demos/knowledge-citation-trace.json`、`knowledge-citation-reply.json`、`knowledge-source.json`、SSE 文本。

## 一条 Provider 失败 Trace

学生问题：“我现在无法保证自己的安全，需要有人陪着我。”

Intent 成功；Risk 的模型辅助返回 LOW，代码明确危险规则合并为 HIGH；RiskEvent、OPEN 工单与 CREATE 审计先提交。Context 走 HUMAN_SUPPORT。Response 的 OpenAI-compatible 请求收到模拟 HTTP 503，ModelCall 记录 FAILED / PROVIDER_HTTP_ERROR；选择固定安全模板、核验模板身份、提交 SAFE_FALLBACK 后 SSE 交付。已创建的工单仍能查询。

此演示使用真正的 Provider 请求映射与 httpx MockTransport，3 次内存 HTTP 请求、0 外部请求。证据：`demos/provider-failure-trace.json`、`provider-failure-case.json` 与 SSE 文本。

## 具体故障用例与结果

固定评测无未处理失败；受控故障保留原始注入与处理结果：

- `cb2-v01`：第一次拒审 → 一次重生成 → ACCEPTED；拒绝文本未进入学生响应或 Trace。
- `cb2-v02`：两次拒审 → SAFE_FALLBACK，保留拒审事件。
- `cb2-v03/v04`：审核异常/绑定错配 → SAFE_FALLBACK。
- `cb2-v05`：有知识命中但生成无引用 → CITATION_REQUIRED / SAFE_FALLBACK。
- `cb2-p01`：生成 HTTP 错误 → 安全兜底；高风险工单保留。
- `cb2-p02`：生成超时 → 取消请求、记录 MODEL_TIMEOUT / SAFE_FALLBACK。
- `cb2-p03`：Intent 非法输出 → 降级后 ACCEPTED，故障仍在事件中。
- `cb2-p04`：Risk 非法输出 → UNKNOWN、人工工单、支持性回复。
- `cb2-p05`：生成截断 → PROVIDER_TRUNCATED / SAFE_FALLBACK。
- `cb2-k05`：资料无命中 → 明确告知、无来源编造、ACCEPTED。

逐条 JSON 保存 raw ModelReply、typed Trace、学生可见响应、准备轮、scorer checks、mismatch_fields 和耗时。汇总：`mock-eval/evaluation.json`、`RESULTS.md`、`SHA256SUMS.txt`。

## 运行与验收入口

在工程根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m campusbridge --port 8014
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate.py verify --packet reports/stage2/20261012-v1/eval-packet
.\.venv\Scripts\python.exe -X utf8 scripts/demo_stage2.py --output reports/stage2/demo-new-001
```

Swagger：http://127.0.0.1:8014/docs。真实模型配置与新版本评测命令见工程 README、`docs/STAGE2_MODEL_RAG_EVAL.md`；当前正式冻结 packet 的 manifest SHA256 为 `de52a4dcfba0b27bd4a1d228ba566ec6a9cec94fd2f35f3297ef8e5df7bfa95a`。修改运行源码后使用新 packet。

本轮在本工程独立实现，未读取或复制商业参考源码/Prompt/素材/数据。未运行真实模型、训练、前端或分布式系统，也未发布仓库。开发停止，等待第二阶段验收。
