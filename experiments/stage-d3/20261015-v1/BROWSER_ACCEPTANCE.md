# 浏览器验收

使用真实 Docker Web 入口、MockModel 与虚构演示账户，通过浏览器实际操作。

- 学生登录、新建对话、校园资料问答；观察 SSE 加载过程与最终回复。
- 打开回复来源详情，查看当前 `compose-acceptance-v2` 资料版本；刷新后对话与引用仍在。
- 提交高风险虚构输入并切换辅导员；执行 OPEN → CLAIMED → IN_PROGRESS → CLOSED，填写记录并确认关闭。
- v3 浏览器闭环工单 `CB-C63F45`，版本 3，4 条历史事件；最终 v4 环境复查 `CB-D839D9` 已关闭且有 4 条历史事件。
- 服务重启后，原 v3 登录态在新 v4 空数据库被拒绝，重新登录后正常。

截图保持原始浏览器像素，无放大或内容修改。浏览器工具输出为 JPEG，归档使用 `.jpg`。`student-source.jpg` 和 `counselor-history-v3.jpg` 来自 v3；`final-counselor-closed.jpg` 来自最终 v4。v3 与 v4 应用源码相同，v4 增加 Redis 启动脚本的正常退出信号转发。

额外观察：较含糊的“希望有老师帮我处理”表达在当前 Mock 路由中未创建额外工单，明确高风险输入正常建单。该用词覆盖问题保留为后续业务测试项，本轮未调整 Agent Prompt 或路由策略。
