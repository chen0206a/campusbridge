# 开源发布检查清单

## 工程和来源

- [x] 公开范围限定为独立CampusBridge工程，父目录及商业材料不进入导出。
- [x] MIT LICENSE包含版权与许可正文；原创Prompt、知识、图标、合成案例和架构来源已记录。
- [x] Python锁定依赖和npm图的版本/许可已整理，安装后由上游保留各自license。
- [x] 未包含模型权重、第三方训练集、字体文件或vendor源码。
- [x] 原开发目录发布前没有Git历史；本次从已审核public-source建立独立发布历史，远端为用户提供的CampusBridge-Agent。

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
- [ ] GitHub托管CI运行：首次push后观察实际结果。当前本地干净环境验证见 [CLEAN_REPRODUCTION.md](CLEAN_REPRODUCTION.md)。

## 确认发布时

1. 使用经过校验的public-source目录作为Git仓库根，不从原开发目录直接全量添加文件。
2. 复核公开导出清单和RELEASE_SHA256SUMS.txt。
3. 按用户确认的仓库名/账号创建仓库，提交前再次运行release audit、archive verify和git staged-file检查。
4. 用户确认公开后才创建/推送公开仓库；仓库URL确定后补CI badge与clone URL。

公开源代码包保持原样。本次用户明确授权推送到CampusBridge-Agent，发布目录只包含经过审核的导出文件；没有增加真实模型实验。
