# Stage 6：动态协作与结果仲裁

CampusBridge 使用轻量任务图组织已有 Agent。TaskPlanner 和 Arbitrator 是确定性的代码策略，复用原 ModelPort、检索、审核与工单模块。通过 `CAMPUSBRIDGE_ORCHESTRATION=sequential|dynamic` 切换编排方式，默认保留 sequential。

![动态编排架构](assets/campusbridge-stage6.png)

## 三条业务路径

| 请求 | 动态执行路径 | 关键决策 |
|---|---|---|
| 普通聊天 | Intent ∥ Risk → 仲裁 → Response → Review → Delivery | 跳过 ContextAgent，直接带入有限历史 |
| 校园知识问答 | Intent ∥ Risk → 仲裁 → Context/RAG → 证据仲裁 → Response → Review → Delivery | 只使用本轮有效来源；无相关证据时说明未找到 |
| 人工支持 / 高风险 | 风险检查点 → Intent ∥ Risk → 仲裁 → 人工事项持久化 → Response → Review → Delivery | LOW 也可以请求人工；HIGH 优先于知识问答 |

显式危险信号先在模型调用前提交 RiskEvent/SupportCase。独立 RiskAgent 更早完成且需要人工时，立即提交风险检查点，不等待 IntentAgent；分析合并后再以唯一 run 关联校准记录。工单仍沿用 OPEN → CLAIMED → IN_PROGRESS → CLOSED。

并发适用于当前消息与有限历史可独立评估的请求。包含“那、刚才、上述、这个、它、继续、之前”等历史指代时保留 Intent → Risk 依赖。两种模式采用相同输入策略：独立分析的 RiskInput.intent 为 null，依赖分析携带 IntentOutput；Sequential 按顺序执行，Dynamic 最多同时执行两个分析任务。

## 协议和调度

`Task` 记录任务身份、角色、启用条件、原因、状态和单调时钟时间；`TaskDependency` 声明父任务及允许终态；`ExecutionPlan` 校验唯一 ID、边关系和无环图。状态包括 PENDING、RUNNING、SUCCEEDED、DEGRADED、FAILED、SKIPPED、BLOCKED、CANCELLED。

调度器检查实际依赖后启动任务。跳过 Context 留下 HISTORY_ALREADY_AVAILABLE 或 HUMAN_PRIORITY；预算阻塞留下 TASK_EXECUTION_BUDGET；降级产物显式为 DEGRADED，依赖边明确允许该状态。一次重生成新增第二组 Response/Review/Delivery 节点，依赖上一组实际终态。

TaskPlanner 先生成分析任务图，再根据结构化分析与仲裁结果扩展后续路径。计划创建、扩展、每个任务开始/结束/跳过/阻塞以及最终快照均存入 RunEvent。离线 verifier 复核 DAG、因果时间、终态和交付审批绑定。

## 并发一致性

Intent/Risk 子任务返回独立的 `CompletedStep` 和类型化 `AgentResult`，由唯一协调器合并并写入业务 RunState/RunEvent。任务局部 ContextVar 关联模型 call_id，乱序返回的 usage/输出校验仍绑定本次请求。模型调用计数、token 预留和调用日志写入通过每轮 writer_lock 串行完成。

每次 Store 操作创建短 SQLAlchemy Session，await 模型时没有共享 Session 或打开的事务。取消时收拢并等待子任务；已经完成的 Risk 结果仍提交人工检查点，迟到生成不交付。

## 仲裁策略

仲裁输入为当前消息、历史、Intent/Risk 结果、失败代码与可选 Context。输出包含最终 route、风险事实、strategy、conflicts、reasons、采用证据的内容 Hash，以及拒绝的 source_id。

- HIGH_RISK_PRIORITY：服务端高风险规则优先；普通意图与高风险冲突转人工支持。
- SUPPORT_NEED_IS_INDEPENDENT_OF_SEVERITY：明确请求辅导员时保留 LOW 严重程度，并创建支持事项。
- STAFF_CONFIRMATION_FOR_DISAGREEMENT：Intent 为 URGENT 但风险未要求人工时转人工确认。
- AGENT_PARTIAL_FAILURE：保留失败角色/原因，用限定的降级结果继续或安全兜底。
- ONLY_CURRENT_VALID_RELEVANT_EVIDENCE：校验来源内容 Hash、唯一 source_id、词面相关性；无有效证据转 NO_HIT。
- UNTRUSTED_SOURCE_SYNTAX_IS_DATA：用户输入的 `[source:...]` 在回复生成视图中变为普通文字，原始消息照常保存。

相关性检查是保守的词面过滤，当前适合固定校园资料。下一步可用独立检索标注集衡量同义词漏召回，再升级评分机制。

## 预算与安全交付

默认每轮最多 16 次普通任务执行、6 次模型请求、131072 个保守 token 预留单位。Token 预留使用输入 UTF-8 字节加消息框架开销及最大输出，预先检查后才发起调用。Mock 单次/整轮超时为 2/15 秒，真实 Provider 默认 20/90 秒。模型调用次数与预算只由协调器分配。

人工事务检查点、仲裁、模板审批和最终 Delivery 属于必要的服务端安全步骤，不消耗普通任务执行配额；它们受固定 DAG（最多 32 个节点）、最多一次重生成和唯一兜底路径约束。即使普通配额耗尽，已识别的风险仍可持久化，安全模板仍可交付。

Response 完整生成候选，OutputReviewer 审核实际文本并绑定 candidate_id、content_hash 与引用 Hash。交付重新核验绑定与本轮来源白名单。拒审候选不会出现在 SSE token、消息历史或学生 Trace 中。审核异常、超时、候选错配、预算耗尽进入固定安全模板审批路径。

## 运行与复现

```powershell
$env:CAMPUSBRIDGE_MODEL_PROVIDER='mock'
$env:CAMPUSBRIDGE_ORCHESTRATION='dynamic'
$env:CAMPUSBRIDGE_MAX_PARALLEL_AGENTS='2'
.\.venv\Scripts\python.exe -X utf8 -m campusbridge --port 8014
```

同校园辅导员的 GET `/api/runs/{run_id}/trace` 可查看 plan/task_event/arbitration；学生端保持业务视图。无需改变前端或工单 API。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
Push-Location frontend
npm test -- --run
Pop-Location
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate.py prepare --output runs/fixed30-packet
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate.py run --packet runs/fixed30-packet --output runs/local-dynamic-001 --orchestration dynamic --mock-delay-ms 80
```

真实实验需从进程环境传入 CAMPUSBRIDGE_ENDPOINT/MODEL/API_KEY/MODEL_PROVIDER/THINKING_MODE，增加 `--provider openai-compatible --allow-real-models --max-estimated-cost-usd`。每次选择新 output 目录，运行前生成 FREEZE，完成后保存原始输出、Trace、计分及 Hash。

## 对照口径

Stage 6 两种模式共享 Stage 5 的角色 Prompt、30+12 条原始案例与 gold、生成参数、评分检查项。共享条件化 RiskInput 是 Stage 6 的输入适配，与 Stage 5 串行 RPC 载荷有差别。Dynamic 同时引入任务选择、并发和证据仲裁，因此结果衡量整套动态策略；Mock 的 80ms 延迟是合成 I/O 夹具，真实延迟单独报告。

两条历史风险 gold 保留 MODERATE，而当前策略将 LOW 严重程度与人工支持需求分别输出；原始分母和失败明细照常保留。自然模型结果与受控故障分开统计，计划结构检查与决策正确性分别报告。
