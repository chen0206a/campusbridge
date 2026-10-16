# 2–3 分钟演示脚本

演示前执行 `docker compose up --build -d`，默认 MockModel + dynamic + BM25。打开 http://127.0.0.1:8080/ 与 `/docs` 两个标签页；也可使用 README 的 SQLite 本地方式。演示使用虚构账号；独立演示可通过新的 Compose 项目名隔离数据卷。

| 时间 | 操作 | 讲解 |
|---|---|---|
| 0:00–0:20 | 学生澜登录，展示历史/新建对话 | “CampusBridge把学生交流、可追溯校园资料和辅导员跟进接成一个完整应用。今天使用离线Mock模型。” |
| 0:20–0:50 | 发送“图书馆预约座位需要做什么？”；观察回复，点来源详情 | “Intent和Risk在输入独立时并行。知识需求触发Context检索，回复使用本轮来源白名单，引用可以核对原文和版本。” |
| 0:50–1:10 | 刷新页面，再发送“那需要提前准备什么？” | “刷新恢复会话。历史指代会建立Intent→Risk依赖，任务跳过和依赖原因都进入Trace。” |
| 1:10–1:30 | 新建会话，发送“我想找辅导员谈谈适应校园的困难” | “人工需求与风险严重程度分开。LOW也能请求工作人员，事项先提交数据库再生成回复。” |
| 1:30–2:15 | 退出，用counselor_yu登录；认领→填写记录→处理中→填写关闭依据→关闭 | “服务端校验身份、状态与版本，相同操作键可幂等重放。四条操作历史形成可查询的人工闭环。” |
| 2:15–2:40 | 展示计划/Trace JSON示例与架构图 | “完整候选经过实际审核并绑定Hash，只有通过审核的内容会进入SSE。调度器记录并行区间、仲裁证据和有限重生成。” |
| 2:40–3:00 | 展示实验表、Docker 状态和压测结果 | “真实模型边界10/12→12/12，任务176→150。单Backend的Mock压测到100用户；D5同日对照吞吐15.08→18.14 RPS，P95 15.29→12.99秒，原始记录可复现。” |

建议的处理记录：“虚构演示：已联系学生，确认校园适应支持需求，并约定下一次交流。”关闭依据：“虚构演示：本次支持事项已完成，已说明后续支持渠道。”

演示后的独立复现：`python scripts/showcase_demo.py --output runs/showcase-001`。输出包含普通、知识、人工、高风险四种计划及工单状态；`python scripts/http_smoke.py --output runs/http-001.json`检查真实HTTP服务并自行停止。

GitHub short description (EN): **Typed multi-agent campus support with dynamic planning, grounded RAG, reviewed SSE delivery and auditable human cases.**

中文简介：**原创校园学生支持多Agent应用，结合动态任务规划、并行分析、证据仲裁、最终回复审核和可审计人工工单。**
