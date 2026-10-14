# CampusBridge Stage 5 Handoff

三项精准修复已实现并完成真实模型回归。沿用现有DeepSeek配置，thinking disabled、temperature 0、max output1024；真实评测运行日为2026-10-13，协议/数据版本标识沿用20261012。

## 主要成果

- 人工支持：Intent/Risk新增独立needs_human_support；明确人工请求进入工单流程，同时保留真实危险严重程度。LOW也可以有人工工单。支持规则覆盖第一人称请求、否定、引用、流程询问，危急风险仍单独强制关注。旧needs_human作为读取/构造别名兼容历史。
- 引用：Stage4旧失败实因为ID前空白，来源本身存在；现在只容忍ID外侧空白，未知ID仍精确拒绝。Response输入显式allowed_source_ids；生成、审核、持久化交付、回复读取、历史恢复均核验本轮持久化Context的来源白名单与quote/hash/revision/document/title。重复引用metadata去重；可在既有预算内重生成一次，SSE仍只交付审核通过的完整候选。
- 超时夹具：指定Agent/指定调用的timeout override取代全局50ms改写。cb2-p02真实联调中Intent/Risk成功，Response单独MODEL_TIMEOUT，按预期兜底；高风险独立测试证明工单在生成失败前已提交并保留。

## 实测结果

| 指标 | Stage4 | Stage5 |
|---|---:|---:|
| 原30严格成功 | 26/30 | 28/30 |
| 自然样本成功 | 17/20 | 18/20 |
| 受控故障成功 | 9/10 | 10/10 |
| route accuracy | 90% | 100% |
| risk accuracy（旧gold） | 90% | 93.33% |
| 交付状态 accuracy | 96.67% | 100% |
| 工单创建 accuracy | 90% | 100% |
| 自然兜底 | 1 | 0 |
| 全部兜底 | 8 | 7 |
| 引用grounding | 4/4 | 5/5 |

原cb2-r03/r04的旧gold定义人工支持请求为MODERATE，新协议将severity与支持需求分开，因此输出LOW+needs_human_support=true、HUMAN_SUPPORT并正确创建工单；这两条仍按旧gold在risk/strict计错。该差异在首次请求前明确冻结，未事后改分。受控集对照包含cb2-p02夹具修复。完整逐字段差值与延迟/token见[STAGE4_VS_STAGE5.md](STAGE4_VS_STAGE5.md)。

新增12条独立固定边界集为10/12：人工支持6/6，知识引用4/6；route/risk/工单创建均100%，已交付引用4/4 grounding。cb5-k05无关查询被通用词召回，正确无资料答复因HIT必须引用条件被拦截；cb5-k06拒绝伪来源时复述了其[source:…]标记，被解析为未知正式引用。两条都在一次重生成后兜底，原始输出保留，未继续调Prompt或重跑。见[FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md)。

Smoke固定5条的route/state/工单均正确，k03、p02、r01通过；strict3/5的两个risk差异同上。原30/新12各只执行一次正式run。

## 运行、预算与产物

| 批次 | 请求数 | 实际耗时 | 峰值未缓存估费 |
|---|---:|---:|---:|
| smoke5 | 18 | 19.28秒 | $0.0046074 |
| real30 | 125 | 142.79秒 | $0.03159 |
| real12 | 52 | 69.74秒 | $0.0156741 |
| 合计 | 195 | 231.81秒（3分52秒） | $0.0518715 |

费用预留合计$0.4207791，预算硬上限$4.50。195次真实调用全部有usage返回；累计输入121,753、输出12,788 tokens。费率按input $0.30/M、output $1.20/M估算，实际账单可能受缓存/时段影响。账户认证在首次请求前成功，API key仅使用进程环境，未落盘。详见REAL_MODEL_CONFIG.json、PREFLIGHT.json、PRE_RUN_PROTOCOL.md、EXECUTION_SUMMARY.json。会话跨夜期间没有模型实验记录，实际调用时间分别11:03、11:07至11:10、11:10至11:11（+08:00）。

所有自然HTTP模型调用成功，无输出截断。受控故障在各自记录中单独标记；新边界的validation failure是引用约束拒绝，并非HTTP错误。每条raw、artifact、score、Trace和usage位于smoke5/、real30/、real12/。

## 测试与完整性

后端159 passed（新增32项）、前端27 passed；针对性41项通过。唯一warning为既有Starlette/httpx deprecation。Mock原30为28/30，只有预声明的risk语义差异；新12为11/12，失败是无关词检索问题。所有Mock运行external requests=0；Mock记录中的actual_api字段沿用模型端口统计，判断外部请求以external_model_requests=0为准。具体测试命令与结果见TEST_RESULTS.json。

275个历史文件hash全部保持，原30输入/gold字节一致；两个最终冻结包分别绑定24个源码文件，模型运行前后及最终校验通过。每个run都有SHA256SUMS，顶层SHA256SUMS记录所有Stage5交付文件。运行前源码快照source-before.tar.gz保留，旧Stage4所有结果/Prompt/评测包未重写。

新增边界目录eval/stage5/20261012-boundary-v1包含cases.jsonl、manifest.json和SHA256SUMS.txt，cases hash为0c906cf6865e2bb37899af054b817b6d7f6da73609c954a2f7d2593cd66f6d96。最终评测包为fixed30-packet-v2、boundary12-packet-v2；初次准备时发现相对路径问题，在首个模型请求前修复并补测试。未完成/已被v2替代的初次准备目录保留，仅用于追踪，不用于实测。

## 改动文件与方法

核心源码：contracts.py（独立需求字段与兼容）、policy.py（请求规则/风险合并）、prompts_v3.py（原创角色规则）、agents.py（白名单和校验）、grounding.py（共享定位校验）、runtime.py（引用有限重生成与调用级timeout）、store.py（本轮引用交付校验）、models.py（Mock v3）、evaluation.py（指定故障注入/新集冻结）、scripts/evaluate.py（新集入口与费用/延迟统计）。tests/test_stage5.py新增32项，docs/STAGE5_PRECISION_FIXES.md说明协议。完整SHA变更清单见CODE_CHANGES.json。

复现离线检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate.py verify --packet reports/stage5/20261012-precision-v1/fixed30-packet-v2
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate.py verify --packet reports/stage5/20261012-precision-v1/boundary12-packet-v2
cd frontend
npm test -- --run
```

任何再次真实run应新建结果目录、配置上述环境变量并显式--allow-real-models；本轮现有结果目录不会覆盖。

下一步优先处理HIT但无相关答案的拒答协议，以及来源拒绝说明中复述标记的问题，采用新的版本化评测；当前无需LoRA。工单权限、审核绑定和SSE全量回归通过。没有架构重写、训练、公开发布或新增复杂服务。完成后停止。
