# 开源发布检查清单

## 工程和来源

- [x] 公开范围限定为CampusBridge源码、文档、许可资产与合成评测资料。
- [x] MIT LICENSE包含版权与许可正文；原创Prompt、知识、图标、合成案例和架构来源已记录。
- [x] Python锁定依赖和npm图的版本/许可已整理，安装后由上游保留各自license。
- [x] 未包含模型权重、第三方训练集、字体文件或vendor源码。
- [x] Git仓库根仅包含经过审核的CampusBridge公开文件。

## 数据、密钥和历史

- [x] `.env`、key形状、Bearer和序列化登录token由公开包检查拦截。
- [x] 数据库、缓存、虚拟环境、构建产物、临时日志与本机绝对路径排除。
- [x] 本地历史报告保留原样；公共历史副本逐文件使用原SHA-256校验。
- [x] 原成绩、gold和历史Prompt没有改写；新评测明确要求新packet/输出目录。
- [x] 演示数据为虚构账号与合成表达；公开演示密码单独注明。

## 可运行与展示

- [x] 中英文README、架构图、Sequence/Plan/Trace、截图与2–3分钟脚本齐全。
- [x] 无API key的MockModel运行方式明确，真实模型接入为服务端可选配置。
- [x] GitHub Actions配置后端Ubuntu/Windows与前端测试/构建，action固定commit、只读权限、无模型secret。
- [x] 首次发布的GitHub托管CI五项检查通过；后续分支仍须单独检查。[首次运行](https://github.com/chen0206a/CampusBridge-Agent/actions/runs/38511431189)。本地干净环境验证见 [CLEAN_REPRODUCTION.md](CLEAN_REPRODUCTION.md)。

## 确认发布时

1. 检查本次变更及公开文件清单，排除开发数据和临时产物。
2. 复核公开导出清单和RELEASE_SHA256SUMS.txt。
3. 提交前运行release audit、archive verify和git staged-file检查。
4. 按本次发布授权推送，检查实际GitHub CI结果。

历史源代码包与实验副本保留原样。新评测使用独立版本的输入包、结果目录与Hash清单。

## Release Candidate 验收（2026-10-16）

- [x] D5 完整历史合并本地 main：`d81cc0a`；RC 独立分支归档后同步本地 main。
- [x] 新克隆/新 venv，锁文件安装、pip check、Mock 演示与 HTTP 通过。
- [x] 后端 355 passed，独立 MySQL/Redis 39 passed，Linux 容器 116 passed，前端 27 passed；类型检查/构建通过。
- [x] 新 Compose 项目/空数据卷实际启动，学生登录、RAG、SSE、工单状态/权限/幂等/Trace 和持久化通过。
- [x] 原历史 Hash 与截图未变；新增发布版 drawio/PNG/SVG 与依赖盘点。
- [x] 当前文件和全部可达 Git 历史单独扫描；报告只记录路径/规则/Object ID，不回显凭据。
- [x] CI 增加完整 fetch 的 source-audit；`.gitignore` 补充运行目录与证书格式。
- [x] 失败首记录保留：npm 共享缓存权限、Windows MySQL 中文路径、Linux 测试初次漏挂 eval，修正环境后通过。
- [ ] 当前 RC 的 GitHub 托管 CI：等待用户确认推送后执行。
- [ ] 远程 tag/Release：等待用户确认；本轮不执行。

最终 SHA、净源码清单和全部证据见 [RELEASE_CANDIDATE_HANDOFF](../RELEASE_CANDIDATE_HANDOFF.md)。现有远程历史和原实验记录均保留。
