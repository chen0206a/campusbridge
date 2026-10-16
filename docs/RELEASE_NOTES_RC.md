# CampusBridge Release Candidate

拟发布标识：`v0.6.0-rc.1`。Python 包版本保留 `0.6.0`；本轮准备发布内容，不创建远程 Release 或 tag。

## 应用与架构

- TaskPlanner 创建类型化任务依赖；独立 Intent/Risk 有限并行，Arbitrator 记录冲突、证据与最终路由，ContextAgent 按需检索。
- 完整候选生成后审核，candidate/content/citation Hash 绑定；有限重生成、安全兜底和审核后 SSE。
- 学生历史会话、知识来源详情、辅导员认领/处理/关闭与审计记录。
- 版本化 BM25 / 可选本地 Dense / RRF Hybrid，校园、有效版本和来源 Hash 校验贯穿交付。
- MySQL 业务事务、Alembic 迁移、独立 SQLite 知识索引、Redis 候选缓存和原子限流。
- Docker Compose 默认 Mock + BM25，一条命令启动。初始化/迁移职责分离、非 root 应用、私有密码卷、回环 Web 入口。

## 实测结果

DeepSeek 历史固定集：Stage 4 26/30，Stage 5 和 Stage 6 均 28/30。Stage 6 Sequential→Dynamic：边界 10/12→12/12，任务 176→150，P50 4.32→3.43s，模型请求 125→126，边界兜底 2→0。两条 MODERATE/LOW 历史风险标签分歧保持原分，人工流程正确创建。[原始评测](EXPERIMENTS.md)

D5 相同 Mock 工作负载、单 Backend、同日优化前后：100 VU 15.08→18.14 RPS，P95 15.29→12.99s，P99 16.62→13.79s。正式四档 3,143 请求零失败/超时。剖析定位同步审计和重复查询后，小范围合并仲裁元数据事务；高并发监控全部完整。[原始数据及资源曲线](../experiments/stage-d5/20261016-v1/HANDOFF.md)

## 安装与演示

```bash
docker compose up --build -d
docker compose ps --all
```

访问 http://127.0.0.1:8080/；`student_lan`、`student_he`、`counselor_yu` 的虚构演示密码为 `CampusDemo-2026!`。本机端口占用可设置 `CAMPUSBRIDGE_WEB_PORT`。停止使用 `docker compose down`，保留数据卷。[2–3 分钟脚本](DEMO_SCRIPT.md)

## RC 验收及运行范围

最终提交、干净克隆安装/HTTP/SSE/工单、自动化测试、历史和发布扫描见 [RELEASE_CANDIDATE_HANDOFF](../RELEASE_CANDIDATE_HANDOFF.md)。源码为 MIT；依赖、镜像、可选模型保留上游许可。[来源](PROVENANCE.md) · [第三方](../THIRD_PARTY_NOTICES.md)

运行配置为一个 Backend。Locust 是同机短时 Mock HTTP 负载，测到 100 VU；高负载尾延迟仍需根据实际部署容量规划。Runtime jobs/会话锁是进程内机制，公开演示账号用于本地体验；公网部署须替换演示认证配置并补齐 TLS/运维策略。新模型实验和进一步性能优化不在本 RC 范围内。
