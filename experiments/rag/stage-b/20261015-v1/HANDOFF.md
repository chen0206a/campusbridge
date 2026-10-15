# CampusBridge RAG Stage B HANDOFF

分支：rag/stage-b-hybrid；起点38eb922。实现、参数选择、一次正式比较均已完成；只本地提交。

## 实现与文件

- src/campusbridge/dense_retrieval.py：可选CPU BGE、批量编码、内容寻址SQLite向量缓存、有效版本/校园过滤、RRF、显式降级与配置工厂。
- src/campusbridge/settings.py、api.py：检索模式/模型路径/阈值/线程/降级配置，复用KnowledgePort与既有导入。
- src/campusbridge/rag_b_evaluation.py：新评分口径、冻结与Hash校验、Dev选参、同协议三模式比较、仲裁前后视图。
- scripts/prepare_rag_b.py、fetch_embedding.py、evaluate_rag_b.py：固定原创Dev/Holdout、校验下载、评测入口。
- scripts/profile_rag_b.py、report_rag_b.py：固定排名的独立时延回放、完整逐条对照与口径复核报告。
- scripts/serve_rag.py、demo_rag_b.py：预算外预热、真实本地Embedding+MockModel的SSE/引用演示。
- tests/test_dense_retrieval.py、test_rag_b_evaluation.py：36项新增测试。
- eval/rag/20261015-stage-b-v1/：原72条副本、32条Dev、32条Holdout、原文证据reference、protocol、provenance、SHA256。
- requirements-rag.lock、pyproject.toml、.env.example、.gitignore：可选依赖和本地配置，排除权重/缓存/索引。
- scripts/release.py：将可选依赖lock纳入导出，并排除额外模型/向量文件后缀。
- README中英文、docs/ARCHITECTURE.md、RAG_STAGE_B.md、RAG_DEPENDENCIES.json、PROVENANCE.md、THIRD_PARTY_NOTICES.md：运行、架构、结果与许可。

## 正式成绩

固定原72条中56条可回答，32条Holdout中24条可回答。其余负例单列。模型为MIT的BAAI/bge-small-zh-v1.5，CPU FP32；512维，固定revision和权重SHA见model-snapshot.json。Dev预定义网格选择Dense/Hybrid阈值均0.50，RRF等权k60、候选20。72条和Holdout未参与选参。

| 集合 / 模式 | Recall@1 | Recall@3 | 有效Recall@5 | MRR@5 |
|---|---:|---:|---:|---:|
| 原72 / BM25 | 60.71% | 76.79% | 76.79% | 0.7143 |
| 原72 / Dense | 76.79% | 91.07% | 91.07% | 0.8601 |
| 原72 / Hybrid | 76.79% | 89.29% | 91.07% | 0.8616 |
| Holdout / BM25 | 62.50% | 83.33% | 83.33% | 0.7431 |
| Holdout / Dense | 70.83% | 85.42% | 85.42% | 0.8056 |
| Holdout / Hybrid | 79.17% | 89.58% | 91.67% | 0.8750 |

[完整表](analysis/COMPARISON.md)包含Top1/Top3来源相关性、分组、无资料/缺事实、仲裁后分数和时延；[全部104条分析](analysis/FAILURE_ANALYSIS.md)与case-deltas.jsonl保留改善及退化。指标为离线检索成绩。

原72条同义口语Recall@3由16.67%升至66.67%（Dense/Hybrid）。例如“借来的书还想多看几天”与“电脑显示连上了，可网页就是打不开”分别恢复图书馆、网络资料。原72条Dense有8条Recall@3改善，Hybrid7条；Holdout两模式各2条改善，Dense有2条退化、Hybrid1条退化。Dense漏掉Holdout08的口语文印查询，Hybrid保留BM25命中；Holdout21跨段落证据覆盖均退化，该条固定第一证据另有标注复核提示，原分保留。

原72条无资料7条的误召回：BM25 1/7、Dense 0/7、Hybrid 1/7。证据不足9条返回材料：9/9、7/9、9/9。Holdout无资料均0/6误召回，缺事实返回材料为2/2、1/2、2/2。相似度没有被当作答案充分性的判据；现有系统尚未新增独立可回答性分类器。

## 口径修正与兼容

Stage A主表确实请求Top5，Recall@5有效；其两个Top3视图的旧Recall@5字段无效，新协议改为null。原结果与Hash原样保留，修正说明追加在analysis/STAGE_A_METRIC_AUDIT.md。

现有Arbitrator会删除3条查询中的正确语义证据：synonym03、synonym12、alias02；Dense的原72条仲裁后Recall@3为85.71%，Hybrid83.93%。未重写仲裁/Agent、修改Prompt或放宽引用校验。

## 时延、资源与验收

正式比较108.52s。其期间完整后端测试也在运行，原时延保留；测试结束后的固定排名时延回放每模式216个样本：BM25 P50/P95 14.80/34.24ms、Dense69.49/83.94ms、Hybrid85.88/121.53ms。回放逐次验证与正式source顺序相同，未改变分数或参数。

模型新进程加载18.67s，19块构建2.85s，峰值RSS457.11MiB，权重91.39MiB；CPU Torch wheel约619MB，完整依赖需要额外磁盘。模型与venv没有进入Git。原应用8篇资料的Dense和Hybrid API/SSE演示均ACCEPTED、含有效引用，Trace见mock-api-real-embedding-demo/。

完整后端：264 passed，1条既有Starlette/httpx弃用warning，201.80s。Stage A原228项及新增36项通过。新增测试在没有ML依赖的基础环境运行；真实本地Embedding演示在独立CPU环境运行。

```bash
python -m pytest -q
python scripts/evaluate_rag_b.py verify --packet experiments/rag/stage-b/20261015-v1/packet
python scripts/verify_archive.py
python scripts/release.py audit --root .
```

完整安装、切换与复现命令见docs/RAG_STAGE_B.md。以serve_rag.py启动语义模式，在请求预算之前预热。

## 默认值与下一步

默认仍为BM25。安装Embedding后的质量优先方案建议显式选Dense、阈值0.50；Hybrid保留可选，Holdout收益更高，但原72条来源精度、误召回和时延不占优。

暂缓Stage C Reranker。先解决语义证据与词面仲裁的衔接、缺事实场景的可回答性，以及Holdout21的独立标注复核。完成后再以新独立Dev判断排序模型的增益。没有新增Agent、前端改动、生成模型请求、训练或GitHub推送。
