# Stage 5 预先固定的评测约定

本轮仅精准修复人工支持路由、RAG 引用和 cb2-p02 超时夹具。先完整离线测试，再执行一次 5-case smoke、一次原固定30对照、一次新固定12边界评测。不会依据运行结果更换用例或修改 gold。

- 沿用 DeepSeek 配置：deepseek-v4-flash、thinking disabled、temperature 0、max output 1024；每轮最多6个模型调用，单次正常20秒，整轮90秒。
- 预算硬上限：smoke $0.50，固定30 $3，新12 $1，总共 $4.50。每次请求先按峰值未缓存价格（input $0.30/M，output $1.20/M）预留，输入字节+framing上限32768。API key只存在进程环境。账户余额接口认证已成功，is_available=true。
- Smoke选择固定为 cb2-r03、cb2-r04、cb2-k03、cb2-p02、cb2-r01，覆盖两条人工支持请求、空白引用、response超时和高风险；不依据未来模型成绩选取。
- 30条 cases.jsonl 与 Stage4逐字节一致，保留旧 gold和原四个主字段计分；已预先确定 cb2-r03/r04 的旧 MODERATE 标签反映“工作人员支持需求”，而 v3 severity只描述危险程度，低风险主动求助应能 LOW+needs_human_support=true。若这两条新结果为LOW，仍在原始 risk accuracy/strict中计错，另外解释语义变化。不会事后改标签或把辅助分数充作原始总分。
- 新12条在任何Stage5模型请求前独立生成并冻结：6条求助边界、6条知识/引用边界；独立标注 severity 与 needs_human_support。
- 函数 score_case 保留原四项判断；只有新12标签额外核验 needs_human_support。引用 grounding仅核验原文与source identity，人工审核质量不从PASS比例推断。
- cb2-p02修复后只对ResponseAgent第1次调用注入50ms deadline；Intent/Risk按正常超时运行。对旧控制结果的对照同时标记夹具版本变化。
- 每个请求保存原始模型输出、解析artifact、候选审核及Trace；不合格候选不进入SSE。所有失败保留。最终再次检查历史275文件hash、冻结包和新增artifact哈希。

费率来源：https://api-docs.deepseek.com/quick_start/pricing/
