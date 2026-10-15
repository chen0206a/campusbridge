# D1开发中发现的问题与修复记录

所有初轮输出与最终验收分开保存，最后一轮为mysql-exact-json-acceptance/。

| 记录 | 实际结果 | 原因与处理 |
|---|---|---|
| mysql-attempt-01 | 12 passed / 14 setup errors | MySQL反射的VARCHAR包含collation，字符串类型比较误报。改为类型/长度检查，并独立验证表选项。公开视图只隐去本地路径，原始文件保留于工程外，Hash见SANITIZATION.json。 |
| mysql-attempt-02 | 26 passed | 当时的迁移样例仅含基础记录；并发、权限、连接池等通过。随后扩展样例覆盖完整审批与Trace。 |
| mysql-acceptance | 34 passed | 新增重启、租户和回滚验收通过；此轮复制样例仍未覆盖完整Trace浮点内容。 |
| mysql-final-acceptance | 34 passed / 1 failed | 完整Trace复制时RunEvent内容Hash不同，迁移原子回滚。Windows启动器打印含emoji的失败文本时又触发GBK编码错误；报告文件先于打印写出，因此原始失败仍完整保存。 |
| mysql-copy-diagnostic | 34 passed / 1 failed | 精确定位到row.event.task_event.task.finished_ms浮点值差异，确认原生MySQL JSON序列化不是审计内容的无损保存格式。 |
| mysql-exact-json-acceptance | 37 passed | 增加d1_0003迁移与ExactJSON，MySQL改用LONGTEXT＋JSON_VALID，保留严格Hash；完整复制、JSON非法写入及所有并发/恢复断言通过，执行前后源码Hash相同。 |

未通过四舍五入、容差或忽略Hash来接受变化。候选、审批、风险和Trace的JSON由应用精确序列化，数据库负责JSON有效性；SQLite保持原JSON存储行为。d1_0003不会恢复此前已被原生JSON改写的历史数值，因此真实数据切换应从保留的SQLite原始来源复制到空目标。

所有测试使用合成账号、MockModel和私有临时MySQL进程。没有外部模型费用；这些测试结论是事务和数据一致性，不是网络吞吐或真实Agent模型质量成绩。
