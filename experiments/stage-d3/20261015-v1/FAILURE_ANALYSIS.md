# 实际失败与修复

保留首次失败日志及最终通过日志；未将失败尝试改写为成功。

| 问题 | 证据与处理 |
|---|---|
| Docker 不存在 | 从官方安装包安装 Docker Desktop 到 D 盘，校验签名和 SHA-256，使用 WSL2 Linux engine。 |
| credential helper 不在 PATH | `compose-up-v1` 失败；为工具进程追加 Docker bin，不写项目密钥。 |
| Docker Hub EOF/下载缓慢 | `compose-up-v2/v3` 保留；Docker Desktop 配置已有本地代理，镜像下载成功。v3 旧源码构建主动中止以固定新源码，耗时不算成功冷启动。 |
| 旧构建镜像标签竞争 | 固定最终源码重新构建，验收各项目采用独立 image tag；43 个安装后 package 文件与当前源码逐字节一致。 |
| Windows 初始化脚本执行位 | MySQL 初始化脚本以镜像 COPY --chmod=0644 安装，由官方 entrypoint source，保留 docker_process_sql 上下文。 |
| tmpfs YAML 逗号 | `invalid-tmpfs` 创建失败；将整项 mount options 加引号，最终 v3/v4 均通过。 |
| npm 缓存 EPERM | 进程内改用外部独立缓存目录；重新 npm ci、27 项测试及生产构建通过。 |
| 测试路径误写 | 首次容器命令指向不存在的 test_web.py；改为现有 test_web_api.py，36 项安装包测试通过。 |
| 高负载下限流测试跨窗口 | 40 请求/20 线程和三次密码哈希可跨越原 1 秒窗口；并发/登录夹具改用 60 秒，阈值及断言不变，单独 1 秒 TTL 到期测试保留。未修改生产限流代码、未跳过失败断言。 |
| Redis 退出信号 | 启动 shell 转发 SIGTERM/INT 并等待 Redis 刷新 AOF；真实重启验证限流 count/剩余 TTL 和缓存恢复。 |

最终主验收为 `compose-v4/result.json`。33.12 秒是空项目数据卷、已有镜像/构建缓存的启动，不包含首次网络下载。未进行无缓存网络速度测量或 D4 压测。
