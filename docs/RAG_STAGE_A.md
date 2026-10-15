# RAG Stage A：检索基线与专项评测

本轮建立独立检索开发评测，保持生产检索和Agent架构。首次结果：[基线报告](../experiments/rag/20261014-stage-a-v1/BASELINE_REPORT.md)、[全部逐条归因](../experiments/rag/20261014-stage-a-v1/FAILURE_ANALYSIS.md)、[交接报告](../experiments/rag/20261014-stage-a-v1/HANDOFF.md)。

## 代码检查与实施范围

| 模块 | 现有行为 | Stage A处理 |
|---|---|---|
| retrieval.py | 版本不可改写；文档/chunk/FTS同事务导入；350字符切块、40重叠；二元中文词+英文词；FTS5 BM25与词面门槛 | 原实现不变，用独立临时库评测；诊断Top5、生产Top3分别记录 |
| ContextAgent | 只在知识路由检索；那/刚才/上述/这个/它触发上一条用户消息扩展 | 调用现有实现，保留history和effective_query |
| Arbitrator | 过滤来源Hash、重复来源及缺词面相关性的结果 | 同次运行记录仲裁前后，分析相关证据是否被过滤 |
| grounding / Response / Reviewer | 本轮source白名单；正文标记、原文quote、版本与Hash绑定 | 保留校验；新增伪造/跨scope标记回归 |
| rag_evaluation.py | 新增离线评测工具 | 标签绑定、冻结包、计分、逐条结果、重复计时与Hash验证 |

现有查询对中文连续字符取二元词，以OR构造MATCH。BM25候选再检查至少两个重合词、英文重合或单一标题词，因此口语概念替换容易无命中。该检查是词面阈值，没有目标事实是否存在的判断。校园与active版本在SQL中筛选；FTS统计来自完整索引。停用版本的来源详情仍可供历史审计查询。

生产路径保持 ContextAgent → KnowledgePort/SQLiteKnowledge → Arbitrator → ResponseAgent → OutputReviewer → 审核交付。Stage A旁路为固定语料/查询 → 同一SQLiteKnowledge → 现有ContextAgent与Arbitrator → 离线计分，不更改API、工单、SSE或Prompt。

## 固定数据与评分

`eval/rag/20261014-stage-a-v1/`：72条查询与8篇补充资料。原8篇资料原字节保留，补充资料仅进入临时评测库；合计16篇、19个chunk。标签含document_id、原文唯一quote、依据与无答案类型。prepare在检索前按原文坐标绑定chunk/source ID，多个证据组与重叠切块均保留。

56条可回答计算宏平均Chunk Recall@1/3/5、Document Recall@K、MRR、Hit@K与证据组覆盖。16条无答案单列空检索判定，异常计失败。来源精度使用相关chunk数/实际返回chunk数，Top3和Top5分列；Top1引用候选来源正确率另列。本轮不生成回复，最终文本引用语义正确率留给后续端到端评测。

关键词、同义/口语、别名、历史、干扰、跨段落分别为12/12/8/8/8/8，无答案16。标签由开发者编写，是开发诊断集；后续优化保持本版冻结，并另建独立验收集评估泛化。

ContextAgent固定校园知识Intent和LOW Risk。每条记录首次观察、一次warm-up和7次warm计时，验证排名稳定；保存Python/SQLite/OS配置，P50/P95使用nearest rank。首次观察不是重建数据库后的冷启动，延迟不包含模型/API和导入。

## 当前成绩

| 指标 | BM25 Stage A v1 |
|---|---:|
| Chunk Recall@1 / @3 / @5 | 60.71% / 76.79% / 76.79% |
| MRR | 0.7143 |
| 无答案空检索判定 | 6/16（37.50%） |
| Top1引用候选来源正确率 | 37/56（66.07%） |
| Top3返回来源精度 | 49/129（37.98%） |
| Warm检索P50 / P95 | 6.76 / 31.31 ms |

精确词Recall@3为100%，同义/口语为16.67%；干扰组Top3为100%、Top1为50%。Top3提高到Top5没有Recall收益，返回来源从129增至175。来源身份/校园/有效版本/Hash检查通过。

缺事实组9条均命中同主题资料，其文本可以帮助回答“资料未知，需要确认”，但检索本身没有可回答性状态。空检索判定与最终正确拒答分开记录。旧Stage4–6的source grounding身份检查和本轮来源相关性精度使用不同口径。

## 离线复现

无需模型/API key；已存在的packet/run拒绝覆盖：

```bash
python scripts/evaluate_rag.py verify --packet experiments/rag/20261014-stage-a-v1/packet
python scripts/evaluate_rag.py run --packet experiments/rag/20261014-stage-a-v1/packet --output runs/rag-bm25-repeat-001
```

从固定数据建立新包：

```bash
python scripts/evaluate_rag.py prepare --dataset eval/rag/20261014-stage-a-v1 --output runs/rag-packet-001
python scripts/evaluate_rag.py run --packet runs/rag-packet-001 --output runs/rag-bm25-001
python -m pytest -q
python scripts/verify_archive.py
python scripts/release.py audit --root .
```

源码绑定覆盖运行Python源码、评测CLI和内置语料；字节Hash、包Hash、参考坐标与source ID在运行前后验证。源码变化后创建新packet，旧包不改写。计时可随机器变化，原始排名与计分结果用于复核。

## 下一阶段接口与技术成果

本轮停止于Stage A。数据表、KnowledgePort、校园隔离、active版本筛选、source_id和Hash约束保持。Stage B可在这些契约下比较Dense/Hybrid，并保留缺事实、历史和干扰切片；本轮未引入新检索模式或reranker。

简历表述：为校园支持RAG建立72条版本化检索评测，支持原文证据到chunk的预绑定、Recall/MRR与无答案分层、Context/仲裁前后诊断、原始结果和Hash复现；通过同义/口语及干扰切片定位召回与排序短板。
