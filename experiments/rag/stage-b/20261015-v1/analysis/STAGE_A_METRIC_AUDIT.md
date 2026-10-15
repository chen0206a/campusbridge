# Stage A 指标口径复核（追加报告）

原Stage A配置明确ranking_top_k=5；实际候选数分布：`{"0": 16, "1": 15, "2": 9, "3": 7, "4": 4, "5": 21}`。主表60.71%/76.79%/76.79%的Recall@1/3/5有效，候选经过阈值过滤后不足5不构成深度错误。

production_context_top3和dynamic_evidence_top3只请求3个结果，旧score_hits仍为它们计算Recall@5，该字段无效。新协议在这两个视图将Recall@5/document_recall@5/hit@5设为null。主表分数不变；Stage A原始JSON、报告与Hash全部保留。

原16条负例拆为无相关资料7条（outside_scope5+campus_isolation2）和主题材料证据不足9条。BM25无资料正确空检索6/7=85.71%，误召回1/7=14.29%；证据不足0/9空检索、9/9返回材料。旧合并口径6/16=37.50%继续保留供追溯。

证据不足中的同主题资料可以支持提示未知或联系工作人员，检索命中本身不证明所问事实存在。Stage B逐条记录RETRIEVED_UNVERIFIED/NO_MATERIAL_RETRIEVED，单列两种负例，未引入基于gold的定位或运行时判断。

新BM25使用与Dense/Hybrid相同的新评分实现、原始72条和同一语料，排名及三项Recall完全复现Stage A；所有返回来源的校园、active revision、原文和Hash检查通过。
