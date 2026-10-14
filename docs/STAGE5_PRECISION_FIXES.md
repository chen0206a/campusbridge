# Stage 5 精准修复

## 人工支持与严重程度

`RiskOutput.level` 描述危险严重程度；`needs_human_support` 决定工作人员跟进。IntentAgent输出独立的`needs_human_support`，RiskAgent结合该输出、当前表达、明确危险规则生成最终结果。明确第一人称请求人工支持的确定性规则只设支持需求，不提高severity；普通情绪、否定请求、引用和流程咨询有单独边界测试。HIGH/UNKNOWN保留人工关注路径。

结构化新字段为`needs_human_support`，旧`needs_human`作为构造/历史反序列化别名及只读属性继续支持。保存后的新artifact使用新字段。低风险人工需求会按原有事务先提交RiskEvent/SupportCase，工单权限、状态机和幂等逻辑不变。

## 本轮引用边界

生成输入额外提供`allowed_source_ids`。生成输出引用解析只去掉标记内ID的外侧空白，精确区分大小写，不修正或猜测未知ID。多个相同引用合为一个metadata项目。原始provider文本在评测记录中保留；规范化后的候选文本重新计算hash并送审核。

共享`grounding.validate_citations`验证marker集合与顺序、来源白名单、quote/hash、revision、document_id和title。生成、最终回复审核、持久化交付、回复读取及历史恢复均验证；交付用本轮持久化ContextArtifact的references，不能借用其他会话、历史回复或全局存在的来源。no-hit/error路径不带来源。

第一条引用校验失败可在既有一次重生成预算内重新生成，带上失败代码和同一来源白名单；第二次失败则安全兜底。已审核完整候选才进入SSE。

## 指定调用的超时夹具

ScenarioModel.configure(response_timeout,target_agent=ResponseAgent,target_call=1)只对选定调用返回50ms timeout override并挂起该次调用。Runtime取正常deadline、指定override和整轮剩余预算的最小值执行真实timeout/cancel路径，其余Agent使用正常deadline。旧Stage4全局50ms方案的原记录仍保存，新的包独立版本化。

## 评测说明

原30条病例与gold字节不变，计分保持四个主字段。v3将severity与支持需求分开，预先记录原cb2-r03/r04的MODERATE标签与新LOW+needs_human_support定义的差异；原始分数保留原标签计分。新的12条固定边界集独立标注这两个维度。每份模型结果保留raw输出、artifact、评分、Trace、费用预留和包哈希。
