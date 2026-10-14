# CampusBridge Stage 4 Handoff — 2026-10-12

## 完成情况

使用 DeepSeek OpenAI-compatible API 完成真实模型 smoke 和一次固定 30 条评测。模型配置为 `deepseek-v4-flash`，endpoint `https://api.deepseek.com`，thinking disabled、JSON mode、temperature 0、每次最多 1,024 输出 token。API key 从已有本地环境读取，仅存在于运行进程环境；未写入源码、配置、报告或日志。正式请求前冻结了新的评测包。

- 初次 Smoke 保留在 `smoke/`：当时未关闭 thinking，普通咨询和知识咨询两条输出被截断并进入安全兜底（2/4）。根据已有 DeepSeek 环境设置关闭 thinking 后，用相同 4 条用例完成修正 Smoke，结果 4/4；覆盖普通咨询、知识引用、高风险支持和审核重生成控制，15 次外部 API 请求，引用 1/1 精确 grounding，无兜底、重生成 1 次。报告：`smoke-thinking-disabled/evaluation.json`，逐条记录位于对应目录。
- 固定评测：26/30；自然用例 17/20；受控故障 9/10。真实 API 请求 124 次，累计评测运行 122.14 秒。报告：`eval-30/evaluation.json`；30 条原始输出及 Trace 均保存在 `eval-30/cb2-*.json`。
- 平均模型调用延迟 805 ms，P50 774 ms，P95 1,778 ms（按 Trace 中所有模型调用统计）。可报告 usage 为输入 62,312、输出 7,910 tokens；其中 2 个实际 API 请求没有 usage 返回，因此费用无法给出完整实账。已知 usage 按 DeepSeek Flash 峰值未缓存输入 $0.30/百万 token、输出 $1.20/百万 token计算为 $0.0282；这是已报告 token 的估算小计，未含无 usage 的两次请求。正式评测预算硬上限 $3.00，单请求输入预检 32 KB、最多 1,024 输出 token，超过预算会在发出 HTTP 请求前停止。Smoke 上限 $0.50。

## 固定标签支持范围

30 条用例支持最终 route、risk、delivery state、case-created 与 failure-recorded 检查；知识用例支持检索状态、引用文档和引用来源 grounding。IntentAgent 的原始 JSON 是否可解析可统计，但用例没有独立 IntentKind gold，因此没有 Intent 分类准确率。引用可做 source-id/原文一致性核验；没有独立的完整语义蕴含标注，因此不把引用 grounding 解释成回答事实正确率。

## 结果与失败

- 全部 route accuracy 90%，risk accuracy 90%，state accuracy 96.7%，case-created accuracy 90%。
- 自然集：route/risk 90%，state 95%，case-created 90%。受控故障集：route/risk/case-created 各 90%，state 100%。
- 4 条自然知识引用均能精确 grounding（4/4）；另有 `cb2-k03` 的一次无效来源引用被拒绝并触发安全兜底。
- 自然 OutputReviewer 决策 25 次均 PASS；控制样本触发 3 次 RETRY，2 次 regeneration。全部 30 条中有 8 条安全兜底，其中 7 条来自受控故障。审核通过比例是行为统计，固定集没有独立 reviewer gold，不能作为审核准确率。
- 失败案例：`cb2-k03` 引用 source-id 不在有效上下文中，安全兜底导致状态/引用文档不符；`cb2-r03`、`cb2-r04` 将明确的人工支持诉求误判为普通低风险咨询；`cb2-p02` 的 timeout 控制把 50 ms 全局 deadline 同时施加到 Intent、Risk、Response，未能隔离测试 ResponseAgent。细节见 [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md)。

## 配置、冻结与验证

无密钥的模型配置记录见 [REAL_MODEL_CONFIG.json](REAL_MODEL_CONFIG.json)。新的包为 `eval-packet-final/`，manifest SHA-256：`3472e0af88b8828559304ee31ecc25035099c89a56bf45c24e8b8552d2301d3d`。运行前后均验证 30 条及 22 个绑定源码文件；运行后再次 verify 通过。重新冻结包时，30 条用例、Prompt、知识库与 schema 相对旧固定包保持逐字节哈希一致；只刷新评测器/源码绑定，旧包保留。

本次 provider 可选 thinking 配置是向后兼容的；默认 MockModel 路径不变。MockModel 对同一个新包回归 30/30，零外部请求。完整回归：后端 pytest 127 passed，前端 Vitest 27 passed；有 1 条 Starlette/httpx deprecation warning。

## 下一步建议

先处理两项小改动并用新的、版本化评测轮次验证：RiskAgent 对“希望有人协助/联系辅导员”的路由规则，以及引用 source-id 白名单校验。随后修复 response-timeout 控制夹具，再做独立评测。当前数据更支持针对路由与 grounding 做小幅 Prompt/验证改进；QLoRA 暂无依据，先积累更有代表性的固定标注与多轮真实基线。

没有运行 QLoRA、训练、数据扩增或第二轮模型实验，也未公开仓库。
