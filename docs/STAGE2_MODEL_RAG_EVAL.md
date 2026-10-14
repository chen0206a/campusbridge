# 第二阶段：ModelPort、知识检索与固定评测

## 模型调用

`OpenAICompatibleProvider` 接受 base endpoint 或完整 `/chat/completions` URL，复用一个 httpx AsyncClient，发送 model、messages、temperature、max_tokens、stream=false；部分服务使用 max_completion_tokens，可通过配置切换。结构化角色附加 JSON mode，Response 返回完整原始文本。JSON mode 的可解析性与字段验证分开处理；本工程还检查枚举、严格布尔值、额外字段、重复键、候选绑定，拒绝代码围栏和自动修复。[官方 Chat Completions 参数](https://developers.openai.com/api/reference/resources/chat)、[结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)

四份原创指令位于 `prompts_v2.py`：Intent 分意图与知识需求，Risk 给人工关注线索，Response 根据上下文生成完整候选，Reviewer 审核实际文字及资料支持。Prompt 后追加本地角色 schema；用户、历史、知识均放在 user 资料区。

环境变量：MODEL_PROVIDER、ENDPOINT、MODEL、API_KEY 均带 `CAMPUSBRIDGE_` 前缀。默认 mock；不隐式使用其他项目的 OPENAI_API_KEY。远程服务使用 HTTPS，endpoint 禁止内嵌凭据、query 和 fragment。API key 用 SecretStr 保存，排除配置序列化；日志、Trace 和报告只记录安全错误代码，不保存 HTTP 错误正文或 Authorization。

每次调用先写 ModelCall/STARTED，再 await，记录 SUCCESS/FAILED/TIMED_OUT/CANCELLED、耗时、usage、finish reason 和输出校验状态。调用成功后角色解析失败记 output_valid=false；文本审核拒绝属于有效 verdict，另由 RunEvent 表达。启动恢复将遗留 STARTED 标记 INTERRUPTED。未知 usage 保留 null，Trace 的 token 总数仅累计已报告部分，并标注 usage_reporting_calls。可解析的失败 envelope（例如长度截断）也保留接口返回的 token usage。

401/403、429、HTTP 错误、传输超时、网络异常、截断、拒答、意外 tool call、畸形响应都转换为固定错误代码。无自动重试，运行时保留每轮 6 次上限与一次重生成；真实模式环境默认单调用 20 秒、整轮 90 秒。Intent 失败降级支持路由；Risk 失败 UNKNOWN 转人工；Response/Reviewer 失败使用事先定义的安全模板。风险工单仍在生成之前独立提交。

## 知识导入与检索

8 份内置 MIT 文本是本工程原创的虚构校园说明：图书馆、学生支持、资助、教务、宿舍报修、失物、无障碍与考试准备。实际校园规则由导入文档提供。

导入字段：document_id、campus_id、title、revision、license、author、origin、text。许可标签和署名随文档保存；外部文档由导入者确认真实许可。工具接受 JSON 数组或 JSONL，单次文件 2 MB、最多 100 文档、单文档 50000 字符。版本不可原地改写，相同内容重复导入幂等，新版本激活并保留旧版本供历史引用查询。文档/切块与 FTS 索引在同一事务中提交；冲突整批回滚。

切块使用 Unicode 字符坐标，默认 350 字符、重叠 40，优先段落边界，保存原文 start/end、切块 Hash。中文使用二元词，英文按词索引，去除固定常用词。查询是绑定参数的 FTS MATCH，BM25 排序，并设最低词面重合条件、最多 3 个来源。SQLite 的 BM25 rank 越小越优先；对外 score 取其相反数。[SQLite FTS5 官方说明](https://www.sqlite.org/fts5.html)

ContextAgent 只在知识路由检索；显式指代时加入上一条用户问题。记录 HIT/NO_HIT/ERROR/SKIPPED、query Hash、命中文档/切块/版本/分数。资料按服务端 campus_id 隔离。历史引用可以查询停用版本，同校园已登录身份才能取原文。

Response 正文必须引用实际检索到的 `[source:source_id]`。代码从对应切块构建 citations：ID、title、revision、quote、Hash。缺引用、未知来源或标记残缺进入失败兜底。Reviewer 同时接收完整 references，审查事实是否得到文本支持。引用身份和 quote 可用代码核实，整句话的语义支持仍由文本审核与评测检查。

审批绑定 candidate_id、content_hash、citations_hash；元数据变化使审批失效。无命中/检索故障时明确告知未找到资料并建议澄清或向工作人员确认，禁止构造来源。生成失败、审核拒绝的内容保留在授权内部 Trace，学生正文与 Trace 不暴露草稿。

## 固定评测

数据版本 `eval/stage2/20261012-fixed-v1`：30 条原创合成用例。普通咨询、多轮、知识、风险各 5 条；审核与 Provider 故障控制各 5 条。使用固定行为参考，逐字段报告 route/risk/工单/终态、历史、检索与引用来源、拒审泄漏、重生成和错误记录。完整 case_pass 是所有适用条件同时成立。

受控故障通过 ScenarioModel 在指定角色处注入，**单列 10 条系统恢复指标**；其输出、fault 名称和 controlled_injection 标签完整保存。自然 20 条用于开发期模型行为检查；真实入口仍区分两种口径。

prepare 保存 cases、知识语料、Prompt、三类 JSON schema 副本及所有运行源码/evaluator 的 byte Hash。manifest/SHA256SUMS 在首次调用前生成，run 前后校验副本和当前源码；已存在的 packet/run 拒绝覆盖。代码或规则更新后创建新 packet。per-case JSON 包含原始 ModelReply、解析后的 typed Trace、准备轮、学生可见响应、引用核验与详细评分。调用失败也保留在总分中。

run 默认强制 MockModel，忽略环境中的真实 Provider 切换；真实评测需要显式 provider 参数及 allow-real-models，缺完整配置返回 PENDING_REAL_CONFIGURATION / 0 请求。评测数据库为临时隔离库，真实请求有 210 次预算（包含多轮准备）且不自动重试。人工判读可在结果旁新增记录，原用例与原输出保持冻结。

## 回归与复现

完整命令在 README。`scripts/demo_stage2.py` 生成一条 HIT/引用/审批/Delivery 轨迹和一条 OpenAI-compatible 503 轨迹（httpx MockTransport、0 外部请求），验证已创建工单和安全交付。旧 59 项测试与 MVP 历史报告原样保留；新增测试覆盖协议错误、token usage、文档版本、原文坐标、校园隔离、引用绑定与固定 packet 防篡改。
