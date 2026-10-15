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
