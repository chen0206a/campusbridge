# Windows CI 引用语义夹具

正式发布过程中，旧 RC 的 Windows/Python 3.12 首轮在跨会话来源用例收到空 citations；v1.0.0 标签首次 CI 在引用重生成用例只观察到一次 Response 调用。相同运行源码的正式 main CI 七项全部通过。失败记录完整保留：

- [RC 首轮](https://github.com/chen0206a/campusbridge/actions/runs/38855117228)
- [正式 main 完整通过](https://github.com/chen0206a/campusbridge/actions/runs/38855367615)
- [v1.0.0 标签首轮](https://github.com/chen0206a/campusbridge/actions/runs/38856297454)

两次失败的套件耗时分别为 644.82s 和 462.37s。原断言未输出 Run Trace，不能仅由空引用确定具体失败阶段。用例直接复用 2s 模型、1s 检索、15s 整轮的演示预算；同步 SQLite/审计 I/O 和调度也计入整轮墙钟预算，存在慢 runner 下先进入兜底的干扰。

最小修复只针对这两个不验证延迟的引用语义用例：独立设置 10s 模型、10s 检索、120s 整轮测试预算。保留来源 ID 精确匹配、跨会话白名单、恰好一次重生成、拒绝文本不出 SSE、最终状态和审核门禁的全部断言；失败增加完整 Trace，避免只有 IndexError 或次数不符。

生产 Settings、Agent、Prompt、scorer、冻结实验和显式超时注入测试均未修改。超时与预算行为仍由原专门用例覆盖；未使用自动重试插件或跳过测试。该调整隔离功能断言与宿主磁盘速度，后续 CI 结果可从 main 的 Actions 查看。

`v1.0.0` 已发布标签保留其原提交，main 的后续提交只改测试夹具、说明与当前文件清单，不强推或重写标签。旧失败日志保留，标签失败项可单独复验并保留 GitHub run attempt 历史。
