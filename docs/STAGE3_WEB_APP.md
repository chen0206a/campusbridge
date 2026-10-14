# 第三阶段 Web 应用

本轮直接实现可操作的学生端和辅导员工作台。数据、鉴权、审批交付和工单状态由原后端负责；新 Web 入口补齐页面数据，并同源服务生产构建。

## 页面与业务链

学生：登录 → 服务端 me → 所属会话列表 → 新建/恢复 → POST SSE → meta/status/token/done → 已提交 run 与 citations → 来源详情。刷新可恢复 token、会话选择和 pending 请求；退出同时撤销服务端 token。

辅导员：登录 → 同校园工单列表 → 状态筛选 → 工单详情与学生表达 → claim/version → process/note → close/依据确认 → CaseEvent 历史。owner、scope、版本、状态和幂等仍由服务端验证。

学生页面展示正文、历史与来源；内部风险判断、模型调用 Trace、Hash、token usage 和调试信息不进入页面。辅导员页面展示支持线索与操作审计，没有模型调试面板。

## 独立 Web API

`src/campusbridge/web_api.py` 调用现有 create_app，再添加：

| 接口 | 用途与校验 |
|---|---|
| GET /api/auth/me | 认证 token，返回实际账号/角色/显示名 |
| POST /api/auth/logout | 事务删除当前 token Hash；原 token 随后 401 |
| GET /api/conversations | 仅当前学生，标题、时间、条数，不包含候选/风险产物 |
| GET /api/cases/{id}/student-context | 先复用 case_detail 的辅导员/campus 校验，再返回脱敏学生表达 |
| GET / | 构建存在时挂载 frontend/dist；原 API 与 docs 路由优先 |

第三阶段在原 API 上补充 Web 能力。历史 packet 保留原来源绑定；新复现生成绑定当前源码的新版本。

## 可靠交互

- 前端使用相对 API URL；开发代理目标由配置控制。
- SSE 解析不修补不完整 JSON；错误帧或缺 done 显式失败，pending 身份保留。
- request key/run_id 随恢复流程保持；meta 后只重放已授权的原 run。
- 请求超时、断连、失败终态、401、403、404、409 都有业务反馈。
- 首轮建会话/发送与工单动作都有同步锁，防止快速点击重复提交。
- 工单冲突刷新详情并保留笔记；结果不确定时复用原操作键。
- 历史读取按四条一批获取审批后的引用，有请求序号和取消保护，避免旧结果盖住新会话。
- React 文本转义；来源标记不是可执行 HTML；移动侧栏关闭时不可聚焦；弹窗焦点仅在打开时设置。

## 实际验收

后端 124 项通过（保留前 119 项）；前端最终 27 项通过；TypeScript 和 Vite 生产构建通过。原始失败记录也保留：初次关闭弹窗测试发现每次输入重置焦点，已修复并通过；浏览器发现错误凭据被误提示为会话过期，已修复并新增回归测试。

浏览器实际使用 MockModel：普通资料对话、多轮、来源弹窗、刷新恢复、登录错误、学生账号隔离、工单 OPEN/CLAIMED/IN_PROGRESS/CLOSED 和四条审计记录。移动端在真实 390px iframe 中验证，学生主区域 width/contentWidth 都为 390，无横向溢出；辅导员主区域 width/contentWidth 都为 375（含滚动条占位）。

截图、JSON 核验、JUnit、版本和 Hash 清单见 [历史实验索引](../experiments/README.md)。运行与详细复现见 [前端 README](../frontend/README.md)。
