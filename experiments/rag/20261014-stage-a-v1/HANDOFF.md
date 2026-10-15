# CampusBridge RAG Stage A HANDOFF
+
+日期：2026-10-14。分支：`rag/stage-a-baseline`，从稳定main `f75c284`建立独立worktree `campusbridge-rag-stage-a`。本轮未推送GitHub，main未修改。
+
+## 完成内容
+
+清理PROVENANCE、RELEASE_CHECKLIST和ASSETS中的无关工程来源叙述，保留MIT、依赖许可、署名、资料和图像来源；更新当前发布检查说明。原第三方通知、许可证、依赖清单原样保留。
+
+新增72条固定开发检索查询，覆盖精确词12、同义口语12、别名8、历史8、干扰8、跨段落8、无答案16。原有8篇语料原字节保留；隔离补充8篇MIT合成资料。16篇/19切块仅进入临时评测库。没有修改生产知识、Prompt、Agent或检索算法。
+
+新增评测CLI与模块：按原文唯一quote绑定相关document/chunk；冻结queries/corpus/reference/config/源码Hash；运行前后verify；拒绝覆盖旧packet和run；保存完整hits、实际query、上下文、仲裁和逐条分数。
+
+## 首次正式结果
+
+| 指标 | BM25 v1 |
+|---|---:|
+| Chunk Recall@1 / @3 / @5 | 60.71% / 76.79% / 76.79% |
+| Document Recall@1 / @3 / @5 | 65.18% / 78.57% / 78.57% |
+| MRR | 0.7143 |
+| 无答案空检索判定 | 6/16（37.50%） |
+| Top1引用候选正确率 | 37/56（66.07%） |
+| Top3来源精度 | 49/129（37.98%） |
+| Warm检索P50 / P95 | 6.76 / 31.31 ms |
+| 正式运行耗时 | 8.12 s |
+| 模型调用 | 0 |
+
+主要短板：同义口语Recall@3仅16.67%，13条查询缺相关Top5证据；6条第一名排序偏差，干扰组Top1仅50%；9条缺具体事实问题仍返回主题资料；“继续”历史指代没有触发当前扩展规则。Top3增至Top5无Recall收益。Context和Arbitrator的Top3计分相同，词面仲裁没有进一步解决本集的问题。
+
+无答案指标是检索层空结果判定；没有测试最终回复是否拒答。来源Hash/身份核验通过，相关性与事实充分性单列。标注是开发者编写的合成开发集，尚未经过独立标注者裁决。Stage4–6的历史source grounding与本次来源相关性使用不同指标定义。
+
+## 验证
+
+- 完整后端：228 passed / 0 failures / 0 errors，210.95秒；25项新增测试。
+- 检索专项与原检索测试：43 passed，22.19秒。
+- 一条原有Starlette/httpx弃用提示，依赖不变。
+- 560份历史实验副本原Hash通过；已有src/eval/experiments逐文件对比通过。
+- 包Hash、source binding、document/chunk坐标和引用校验通过；公开审计无异常。
+- 前端未变，本轮未重复前端测试；回归覆盖现有后端API、动态编排、工单、审核和SSE。
+
+验证详情：`VALIDATION.json`。逐条结果：`bm25-baseline-v1/results.jsonl`；分数：`summary.json`；完整归因：`FAILURE_ANALYSIS.md`；口径：`BASELINE_REPORT.md`和`docs/RAG_STAGE_A.md`。
+
+## 复现
+
+```bash
+python scripts/evaluate_rag.py verify --packet experiments/rag/20261014-stage-a-v1/packet
+python scripts/evaluate_rag.py run --packet experiments/rag/20261014-stage-a-v1/packet --output runs/rag-bm25-repeat-001
+python -m pytest -q
+python scripts/verify_archive.py
+python scripts/release.py audit --root .
+```
+
+源码改变后创建新packet，旧包和结果保持。RELEASE_SHA256SUMS更新当前分支清单；原历史archive/index/packet/hash文件未修改。
+
+## 文件修改列表
+
+修改现有：README.md、README.en.md、docs/PROVENANCE.md、docs/RELEASE_CHECKLIST.md、docs/ASSETS.md、RELEASE_SHA256SUMS.txt。
+
+新增源码/CLI/测试：src/campusbridge/rag_evaluation.py、scripts/evaluate_rag.py、tests/test_rag_evaluation.py。
+
+新增说明：docs/RAG_STAGE_A.md。
+
+新增数据及实验文件如下（Handoff及目录级SHA另列）：
+- `eval/rag/20261014-stage-a-v1/DATASET.md`
- `eval/rag/20261014-stage-a-v1/documents.json`
- `eval/rag/20261014-stage-a-v1/provenance.json`
- `eval/rag/20261014-stage-a-v1/queries.jsonl`
- `experiments/rag/20261014-stage-a-v1/BASELINE_REPORT.md`
- `experiments/rag/20261014-stage-a-v1/FAILURE_ANALYSIS.md`
- `experiments/rag/20261014-stage-a-v1/VALIDATION.json`
- `experiments/rag/20261014-stage-a-v1/bm25-baseline-v1/SHA256SUMS.txt`
- `experiments/rag/20261014-stage-a-v1/bm25-baseline-v1/config.json`
- `experiments/rag/20261014-stage-a-v1/bm25-baseline-v1/results.jsonl`
- `experiments/rag/20261014-stage-a-v1/bm25-baseline-v1/run-manifest.json`
- `experiments/rag/20261014-stage-a-v1/bm25-baseline-v1/summary.json`
- `experiments/rag/20261014-stage-a-v1/packet/DATASET.md`
- `experiments/rag/20261014-stage-a-v1/packet/SHA256SUMS.txt`
- `experiments/rag/20261014-stage-a-v1/packet/config.json`
- `experiments/rag/20261014-stage-a-v1/packet/corpus.json`
- `experiments/rag/20261014-stage-a-v1/packet/documents.json`
- `experiments/rag/20261014-stage-a-v1/packet/manifest.json`
- `experiments/rag/20261014-stage-a-v1/packet/provenance.json`
- `experiments/rag/20261014-stage-a-v1/packet/queries.jsonl`
- `experiments/rag/20261014-stage-a-v1/packet/references.jsonl`
- `experiments/rag/20261014-stage-a-v1/packet/source-bindings.json`
+
+另新增本HANDOFF、`eval/rag/20261014-stage-a-v1/SHA256SUMS.txt`和`experiments/rag/20261014-stage-a-v1/SHA256SUMS.txt`。
+
+## 剩余事项与停止点
+
+Stage A完成，停在验收点。现有证据支持下一轮比较Dense/Hybrid，但尚未实现，也没有升级后成绩。后续保持校园/版本/source约束，保留缺事实切片，单独解决历史扩展；只有候选召回改善且排序仍差时再评估reranker。无真实模型评测、无前端修改、无模型下载、无新Agent、无GitHub推送。
+