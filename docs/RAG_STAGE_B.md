# RAG Stage B：本地语义检索与排名融合

Stage B 从 Stage A 提交 `38eb922` 分出，分支 `rag/stage-b-hybrid`。保留 SQLite BM25，通过 KnowledgePort 增加 Dense、Hybrid，Agent Prompt 和业务流程保持一致。

[完整指标](../experiments/rag/stage-b/20261015-v1/analysis/COMPARISON.md) · [104条逐条归因](../experiments/rag/stage-b/20261015-v1/analysis/FAILURE_ANALYSIS.md) · [Stage A口径复核](../experiments/rag/stage-b/20261015-v1/analysis/STAGE_A_METRIC_AUDIT.md) · [交付报告](../experiments/rag/stage-b/20261015-v1/HANDOFF.md)

## 检索实现

| 环节 | 实现 |
|---|---|
| BM25 | 原SQLiteKnowledge，FTS5、中文二元词、词面门槛保持原样 |
| Dense | LocalBGE在CPU FP32上批量编码，CLS pooling、L2 normalization，余弦相似度 |
| Hybrid | 每路按source_id去重，等权RRF：sum(1/(60+rank))，每路最多20个候选 |
| 缓存 | 独立embedding_cache表；键包含模型身份、title+chunk文本Hash；float32 BLOB带维度与校验Hash |
| 有效证据 | 每次读取当前校园active文档，编码后再次过滤有效source；停用向量不作为当前成员 |
| 引用 | 原source_id、revision、chunk坐标、原文Hash；复用run白名单、审核和交付校验 |

19块仅38,912字节向量负载，短SQLite连接和内存排序即可。没有引入向量服务。编码在数据库事务外、线程内执行，使用独立Session；锁保护模型和索引构建。

导入新版本后，下次构建/查询按最新active集合补充向量；相同文本复用缓存，修改标题/正文或更换模型会重新编码。旧版本来源详情继续服务历史引用。查询向量不缓存，Warm包含实际query编码。

模型或缓存失效抛出RetrievalUnavailable；显式fallback=bm25时记录降级日志并执行BM25。默认error。空查询不加载模型。

## 模型与成本

[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，MIT许可，[官方说明](https://bge-model.com/bge/bge_v1_v1.5.html)。固定revision `7999e1d3359715c523056ef9478215996d62a620`，24M参数、512维、最大512 tokens。Query使用官方检索前缀，document使用标题加正文。

权重95,827,648字节，SHA256 `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`。模型与venv位于工程外；仓库只记录版本与Hash。API不下载模型。

实测Windows/Python3.12.15、Torch2.8.0+cpu、Transformers4.57.1、NumPy2.2.6，CPU两线程、batch8。CPU Torch安装wheel约619MB，环境磁盘占用另含依赖；模型约91.39MiB。新进程加载18.67s，19块建索引2.85s，RSS峰值457.11MiB。无付费API、GPU或训练。

serve_rag.py在请求预算之外预热，避免初次加载触发ContextAgent的1秒预算。取消等待会丢弃迟到结果；CPU线程可能完成当前编码并写入内容缓存，不写Agent业务状态。

## 固定集与参数选择

eval/rag/20261015-stage-b-v1/在首次Embedding调用前固定：原72条逐字节副本、32条Dev、32条Holdout、同一16篇语料、原文绑定的reference、protocol与SHA256。

Dev/Holdout各含24条可回答、4条域外、2条校园隔离、2条主题相关但缺事实查询，覆盖精确词、口语/同义、别名、指代、干扰和跨段落。三份集合没有相同校园/查询/历史组合。标签由开发者编写；Holdout21号第一证据的选择已在报告中单列复核提示。

预定义阈值0.50/0.60/0.70/0.80；最大化 `0.5*Dev Recall@3 + 0.5*Dev合并负例空检索率`，同分选更高阈值。RRF k=60、等权、候选20、模型与前缀固定。两模式均选择0.50，全部trial及Dev原始候选保留。随后仅一次正式评测原72条和Holdout，未基于正式成绩调参。

Recall@5只用于请求深度5的ranking视图，Top3视图的Recall@5为null；MRR在ranking视图截断5。无相关资料和证据不足分别统计。相似材料标记RETRIEVED_UNVERIFIED，不把语义相似当作事实充分性。

## 安装与运行

默认BM25无需模型或API key：

```bash
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pytest -q
```

可选CPU环境（Windows/Linux）：

```bash
python -m pip install -r requirements-rag.lock
python scripts/fetch_embedding.py --output models/bge-small-zh-v1.5
```

下载脚本校验固定官方revision的LFS SHA256或Git blob ID。PowerShell启动：

```powershell
$env:CAMPUSBRIDGE_MODEL_PROVIDER='mock'
$env:CAMPUSBRIDGE_RETRIEVAL_MODE='dense' # hybrid / bm25 也可
$env:CAMPUSBRIDGE_EMBEDDING_MODEL_PATH='models/bge-small-zh-v1.5'
$env:CAMPUSBRIDGE_DENSE_MIN_SCORE='0.50'
$env:CAMPUSBRIDGE_RETRIEVAL_FALLBACK='error' # 显式降级用 bm25
python scripts/serve_rag.py --port 8014
```

设置项dense_min_score的初始0.65是未调参部署值；本轮实验显式采用Dev冻结值0.50。默认检索模式仍为bm25。

## 复现

已有输出目录拒绝覆盖。验证与当前源码复现：

```bash
python scripts/evaluate_rag_b.py verify --packet experiments/rag/stage-b/20261015-v1/packet
python scripts/verify_archive.py
python scripts/evaluate_rag_b.py tune --packet experiments/rag/stage-b/20261015-v1/packet --model-path models/bge-small-zh-v1.5 --output runs/rag-b-selection-001
python scripts/evaluate_rag_b.py run --packet experiments/rag/stage-b/20261015-v1/packet --selection runs/rag-b-selection-001 --model-path models/bge-small-zh-v1.5 --output runs/rag-b-comparison-001
python scripts/demo_rag_b.py --model-path models/bge-small-zh-v1.5 --threshold 0.50 --output runs/rag-b-api-demo-001
```

源码改变后保留旧packet，创建新绑定：

```bash
python scripts/evaluate_rag_b.py freeze --packet runs/rag-b-packet-002
```

旧Stage A packet绑定38eb922；执行旧CLI的源码校验时使用原Stage A worktree。Stage B验证旧文件Hash并使用其原数据。

## 结论

原72条Recall@3：76.79% / 91.07% / 89.29%；同义词组16.67%→66.67%。Holdout：83.33% / 85.42% / 89.58%。改善、退化与逐条输出全部归档。

原Arbitrator仍使用词面门槛，Dense在原72条仲裁后Recall@3为85.71%，Hybrid为83.93%。本轮保留该实现。默认继续零依赖BM25；已安装Embedding时建议质量优先配置Dense，Hybrid保持可选。先处理语义命中与仲裁、证据充分性的衔接，再评估Reranker。
