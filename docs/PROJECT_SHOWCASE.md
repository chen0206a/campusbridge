# CampusBridge · 项目展示

原创学生支持Web应用，通过动态多Agent协作把交流、可核对的校园知识和辅导员人工跟进连接起来。主流程与操作都可在本地Mock模式体验。

## 学生对话

发布准备阶段实际浏览器截图，使用当前MockModel + dynamic编排、虚构学生澜；页面保留来源引用与刷新恢复。

![学生对话](assets/showcase/student-chat.jpg)

![来源详情](assets/showcase/source-detail.jpg)

## 辅导员工作台

以下桌面图来自原Stage3浏览器验收，保持原字节；界面设计与当前工作台一致，历史工单为虚构内容。

![辅导员工作台](assets/showcase/counselor-closed.jpg)

## 完整工单流程

| 状态 | 服务端行为 |
|---|---|
| OPEN | 人工事项已独立提交，生成异常仍可查询 |
| CLAIMED | 认领验证身份、校园、owner、版本和幂等键 |
| IN_PROGRESS | 保存工作人员处理意见与审计记录 |
| CLOSED | 关闭依据确认、版本推进与完整操作历史 |

![OPEN](assets/showcase/case-open-desktop.jpg)

![CLAIMED](assets/showcase/case-claimed-desktop.jpg)

![IN_PROGRESS](assets/showcase/case-in-progress-desktop.jpg)

![CLOSED 与历史](assets/showcase/case-closed-desktop.jpg)

发布准备阶段重新在浏览器走通了上述四种状态，当前窄屏截图另存为 [OPEN](assets/showcase/case-open.jpg)、[CLAIMED](assets/showcase/case-claimed.jpg)、[IN_PROGRESS](assets/showcase/case-in-progress.jpg)、[CLOSED](assets/showcase/case-closed.jpg)。

## 展示方式

学生登录→图书馆问题→来源原文→刷新/继续→人工支持请求→辅导员认领/记录/关闭→四条操作历史→任务图与仲裁Trace→真实实验表。

[2–3分钟逐步讲解](DEMO_SCRIPT.md) · [Sequence/ExecutionPlan/Trace](EXECUTION_EXAMPLES.md) · [实验结果](EXPERIMENTS.md) · [简历介绍](PORTFOLIO.md)

当前发布回归后端203项、前端27项通过。图片来源与Hash见 [asset manifest](assets/showcase/manifest.json)，资产许可见 [ASSETS.md](ASSETS.md)。
