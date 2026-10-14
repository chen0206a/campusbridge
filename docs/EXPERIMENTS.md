# 真实实验与复现

Stage4完成DeepSeek联调；Stage5精确修复人工支持路由、来源校验与Response超时夹具；Stage6以同一批原始30+12条gold比较顺序/动态编排。每条输出、Trace、检查项与失败保留，公开副本的字节Hash见 [实验索引](../experiments/README.md)。

## 历史成绩

| 版本 | 固定30 | 自然20 | 受控故障10 | 边界12 |
|---|---:|---:|---:|---:|
| Stage4 | 26/30 | 17/20 | 9/10 | — |
| Stage5 | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage6 Sequential | 28/30 | 18/20 | 10/10 | 10/12 |
| Stage6 Dynamic | 28/30 | 18/20 | 10/10 | 12/12 |

历史risk标签分歧：cb2-r03/r04的gold为MODERATE。当前输出LOW + needs_human_support=true，正确转人工并创建事项；原始评分仍记录两个risk失败。参考保持原样。

## Stage6成对指标

| 指标 | 固定30 Sequential | 固定30 Dynamic | 边界12 Sequential | 边界12 Dynamic |
|---|---:|---:|---:|---:|
| 成功 | 28/30 | 28/30 | 10/12 | 12/12 |
| 路由/工单准确率 | 100%/100% | 100%/100% | 100%/100% | 100%/100% |
| 风险准确率 | 93.33% | 93.33% | 100% | 100% |
| Agent任务执行 | 176 | 150 | 65 | 59 |
| 实际模型请求 | 125 | 126 | 52 | 52 |
| 输入/输出tokens | 72736/7782 | 73577/7993 | 36726/3707 | 35647/3791 |
| 平均case延迟 | 4.752s | 4.069s | 5.183s | 4.651s |
| P50/P95 | 4.321/9.168s | 3.431/8.654s | 5.185/6.106s | 4.209/5.363s |
| 兜底/重生成 | 7/3 | 7/4 | 2/2 | 0/0 |
| 并行分析轮次 | 0/35 | 30/35 | 0/13 | 13/13 |
| 来源grounding | 100% | 100% | 100% | 100% |
| 任务图完整性 | 100% | 100% | 100% | 100% |

Agent任务执行不含跳过、Delivery和模板审批。多轮准备计入token与请求数，case延迟包含准备轮次；P50/P95使用同一nearest-rank实现。任务图检查复核依赖、区间、终态和交付绑定；最终业务正确性由原gold计分。

部署alias deepseek-v4-flash，2026-10-13核验时官方映射V4.1-Flash；thinking disabled、temperature=0、JSON mode、max output=1024、调用20s/整轮90s、每轮最多6次模型调用。包括两种模式各4条smoke的6次真实运行合计438.387s、387次请求，按峰值未缓存费率估算0.1022214 USD，实际账单可能因缓存/时段更低。[官方定价](https://api-docs.deepseek.com/quick_start/pricing/)

Stage6两个模式使用相同角色Prompt、原始案例/gold、生成参数和评分检查，以及共享条件化RiskInput。此输入适配与Stage5 RPC载荷有差别。Dynamic同时引入并发、跳过和证据仲裁，因此成对结果衡量完整策略；单次API延迟作为本次记录展示。

## 具体失败和恢复

- Stage4：两条人工请求路由失败、cb2-k03引用失败、cb2-p02全局超时夹具干扰早期角色。
- Stage5：人工需求独立于严重程度、来源白名单与指定调用超时落地；固定集28/30。边界cb5-k05仍命中无关知识，cb5-k06复述用户伪造来源标记，均兜底。
- Stage6 Dynamic：过滤无关证据并明确NO_HIT；仅在生成视图将用户来源标记当普通数据，保留原输入。边界12/12、兜底0。cb2-k02首次漏引用，经一次重生成恢复，固定集请求增加1。

## 离线复现（默认）

```bash
python scripts/verify_archive.py
python scripts/evaluate.py prepare --output runs/fixed30-packet
python scripts/evaluate.py run --packet runs/fixed30-packet --output runs/mock-sequential-30 --orchestration sequential --mock-delay-ms 80
python scripts/evaluate.py run --packet runs/fixed30-packet --output runs/mock-dynamic-30 --orchestration dynamic --mock-delay-ms 80
python scripts/evaluate.py prepare --output runs/boundary12-packet --cases-file eval/stage5/20261012-boundary-v1/cases.jsonl --dataset-kind stage5-boundary
python scripts/evaluate.py run --packet runs/boundary12-packet --output runs/mock-dynamic-12 --orchestration dynamic --mock-delay-ms 80
```

Mock的80ms是固定I/O夹具，分数与真实模型结果分别记录。现存packet/run拒绝覆盖，新复现绑定当前源码；历史副本可通过original byte hash独立验证。

## 可选真实复现

在服务端进程环境配置endpoint/model/API key、MODEL_PROVIDER=openai-compatible与THINKING_MODE=disabled，使用上面新创建的packet；两种模式保持同一配置。每次指定新输出目录和费用上限：

```bash
python scripts/evaluate.py run --packet runs/fixed30-packet --output runs/real-sequential-30 --provider openai-compatible --allow-real-models --orchestration sequential --max-estimated-cost-usd 3
python scripts/evaluate.py run --packet runs/fixed30-packet --output runs/real-dynamic-30 --provider openai-compatible --allow-real-models --orchestration dynamic --max-estimated-cost-usd 3
```

源码冻结、运行前后verify、请求预留费用和实际usage均写入结果。公开发布准备阶段只运行离线验证，没有新增真实模型成绩。
