# 来源与开源范围

CampusBridge 是校园学生支持应用，工程始于 2026-10-12。项目源码、角色指令、知识资料、演示数据和界面资产的来源与许可如下。

以下内容由项目开发过程编写：

- campusbridge 命名空间的源代码、数据模型、Agent 协议和 API。
- prompts.py / prompts_v2.py / prompts_v3.py 的角色指令，policy.py 的安全模板和规则，以及后续原创规划/仲裁协议。
- knowledge.py 与 resources/knowledge-v2.json 中的虚构校园参考卡片。
- 演示账号、虚构学生、测试输入、审核拒绝样例及人工处理意见。
- README、架构图、接口文档和验证脚本。

这些文件按项目 MIT LICENSE 提供。参考卡、演示数据和 Trace 均为合成内容，没有采集真实学生对话或第三方咨询语料。演示密码是明确公开的虚构开发数据，不是外部系统凭据。

FastAPI、Pydantic、SQLAlchemy、uvicorn、pytest、httpx 等通过包管理器安装，保留各自许可证；运行依赖版本锁定在 requirements.lock。虚拟环境不随工程源码发布。[第三方许可](../THIRD_PARTY_NOTICES.md)、[初始依赖清单](DEPENDENCY_LICENSES.json)、[数据库依赖](DATABASE_DEPENDENCIES.json) 与 [RAG 可选依赖](RAG_DEPENDENCIES.json) 保留分阶段记录；当前锁文件和 RC 依赖审计列出最终版本，npm 图按 frontend/package-lock.json 核对。

公开导出以 scripts/release.py 的允许清单为准，包含项目代码、文档、素材、合成案例和实验公共副本。原始历史模型成绩、输出与Prompt保持原字节，实验索引按原Hash验证每份副本。开发数据库、.env、虚拟环境、缓存和临时日志排除。

资产来源见 [ASSETS.md](ASSETS.md)，发布检查见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)。专项检索评测的合成资料与标注来源记录在对应版本的 provenance 文件中。

RAG Stage B新增的Dev/Holdout为本项目编写的虚构校园检索查询，首次Embedding调用前固定；知识原文复用Stage A合成资料。BGE模型的独立MIT许可、下载来源和可选推理依赖见[RAG实现说明](RAG_STAGE_B.md)及[第三方许可](../THIRD_PARTY_NOTICES.md)。
