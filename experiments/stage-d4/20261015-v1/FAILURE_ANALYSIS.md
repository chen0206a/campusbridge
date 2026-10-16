# 问题记录与处理

| 观察 | 处理与最终结果 |
|---|---|
| 首次后端回归 31 个 Redis setup error | PATH 指向既有 Windows Redis，stdin 配置启动失败。新增显式 Docker Redis 测试命令，不改已安装服务。42 项 Redis/D4 定向验证通过，完整重跑 340 passed /20 opt-in skipped。首次失败日志保留。 |
| MySQL 首个私有目录启动失败 | 初始化成功但服务启动失败，首次 scratch 位于中文路径；改用新的 ASCII scratch 后 38 项通过。保留首次日志，未覆盖旧目录、未修改 MySQL 服务或数据。未将路径因素扩写成对所有 MySQL 版本的结论。 |
| 首次 Linux 容器测试误选默认镜像 | 手工命令遗漏 CAMPUSBRIDGE_IMAGE_TAG，使用 D3 默认镜像而找不到 diagnostics。指定与正式运行相同的 cb-d4-load-v1 镜像后 45 项通过；增加夹具测试后最终 46 项通过。正式负载驱动本身已固定 image tag，不受该手工命令影响。 |
| 100 VU 有两次监控失败 | 业务 909 请求均完成；监控单独记录 2 个 RuntimeError，最后失败日志为容器内 /ops/metrics 的 5 秒读取超时。该档有 6 个有效资源样本，资源峰值是离散观察值，不能补造丢失区间。 |
| 负载增加而吞吐近乎持平 | 10→100 VU：12.47→15.44 RPS，P95 1.49→15.75s。结果显示单实例排队/饱和现象；需要后续剖析同步 Store/DB 热路径、事件循环和线程池，当前采样不足以确定唯一瓶颈。 |
| 期末缓存统计与末次周期样本 | analysis-v1 使用最后周期采样；最终 analysis 改用已保存的每档 drain 完成后 gauges，30 VU hit 189→190。原始请求和模型行为未改、负载未重跑；两版分析均保留。 |

所有档错误率/超时率为 0，未触发预先固定的 5%/20s 升压停止阈值。没有第二轮调参或选择更好成绩。继承的 Starlette/httpx 测试客户端 deprecation warning 仍可见。
