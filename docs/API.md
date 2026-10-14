# CampusBridge API v0.1

除健康检查、Swagger/OpenAPI 和登录外，接口使用 HTTP Bearer token。服务端从 token 查 Account，角色、校园和工单 actor 均来源于服务端。DTO 拒绝额外字段；验证错误不回显提交的密码或消息。

| 方法 | 路径 | 权限/行为 |
|---|---|---|
| GET | /health | 健康、实际 Provider 类型与知识 backend |
| POST | /api/auth/login | username/password，返回随机 access_token |
| POST | /api/conversations | 学生创建自己的对话 |
| GET | /api/conversations/{id} | 所属学生查看消息，助手文本重新核对审批 |
| POST | /api/conversations/{id}/messages | 所属学生发消息，等待完整链并返回已审核 reply |
| POST | /api/conversations/{id}/stream | 所属学生，审核后 SSE |
| GET | /api/runs/{id} | 所属学生或同校园辅导员；未完成 202，失败 503，完成已审核结果 |
| GET | /api/runs/{id}/trace | 学生安全摘要/同校园辅导员完整 Trace |
| GET | /api/runs/{id}/stream | 所属学生重放，复用原结果 |
| GET | /api/cases | 辅导员查询本校园工单 |
| GET | /api/cases/{id} | 本校园工单、risk、处理事件 |
| POST | /api/cases/{id}/{claim,process,close} | 辅导员状态操作；处理/关闭要求 owner |

消息 body：`{"message":"想聊聊学习安排"}`。消息与工单 POST 操作都要求 Idempotency-Key，请为每个新操作使用独立键。

工单 body：`{"expected_version":1,"note":"已人工联系并记录下一次跟进安排"}`。claim 可以省略 note；process/close 的去空白意见至少 3 个字符。

状态：OPEN/version0 → CLAIMED/version1 → IN_PROGRESS/version2 → CLOSED/version3。IN_PROGRESS 允许新键追加意见，version 继续增加。重放返回原操作的 state/version，当前最新状态通过 GET 查询。

SSE：meta(run_id)、status(PROCESSING)、token(text)、done(run_id/state/message_id/content_hash)。审核结束前没有候选 token；故障为 error(REPLY_UNAVAILABLE)，无伪造 done。重放完整文本时客户端按 run_id/message_id 区分重复展示，当前没有 cursor 级断点传输。

401：无效或过期凭证；403：角色或负责人限制；404：资源不在 scope；409：幂等、状态或版本冲突；422：请求契约错误；503：持久化/运行不可用。模型阶段异常通常产生已审核模板结果，状态为 SAFE_FALLBACK，原因保存在 Trace。
## 第二阶段新增字段与接口

原学生/辅导员与 SSE 路径保持。审核后的 JSON reply 新增 `citations`：每项包含 source_id、document_id、title、revision、quote、source_hash。SSE 正文含来源标记，结束后 GET `/api/runs/{id}` 获取结构化引用。

GET `/api/knowledge/sources/{source_id}`：Bearer 认证，同校园 scope；返回原文切块、Unicode 坐标、版本、Hash、许可、作者和版本激活状态。已停用版本仍可供历史引用查询。未知来源/跨校园返回 404，未登录返回 401。

Trace 新增 `calls` 和 `usage_reporting_calls`：调用角色、Provider/model、状态、耗时、usage、输出校验和安全错误代码。学生权限下仍隐藏草稿正文与内部风险产物。`/health` 返回实际 Provider 类型及知识 backend。

