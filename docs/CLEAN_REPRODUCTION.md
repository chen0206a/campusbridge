# 干净目录复现

发布准备使用独立公开源代码导出和新建Python 3.12环境，不复用工程虚拟环境或开发数据库。依赖从锁文件通过包管理器安装，前端使用npm ci。

## 实际结果（2026-10-13）

| 检查 | 结果 |
|---|---|
| 独立环境 | Windows、CPython 3.12.15、Node 24.15.0 / npm 11.12.1、全新venv与独立npm依赖目录 |
| 锁定依赖安装 | requirements.lock安装成功；新环境无工程旧site-packages |
| 项目安装 | campusbridge 0.6.0 editable安装成功 |
| 后端完整测试 | **203 passed / 0 failed / 0 errors**，181.41秒，1条既有Starlette/httpx弃用提示 |
| 前端测试 | **27 passed**，3个测试文件 |
| 前端构建 | TypeScript检查与Vite生产构建通过 |
| npm安装审计 | 0个漏洞（本次包管理器报告） |
| 动态Mock演示 | ordinary / rag / human / risk四种路径均ACCEPTED，0外部模型请求 |
| 人工工单 | OPEN→CLAIMED→IN_PROGRESS→CLOSED |
| 真实HTTP临时服务 | health、Swagger/OpenAPI 200、SSE done、审核交付与工单通过；自身服务停止 |
| 公共历史验证 | 560个副本逐文件原字节SHA通过 |

公开源代码先由允许清单导出，再从独立目录安装与测试。开发数据库没有复制，CAMPUSBRIDGE_API_KEY为空，默认/演示ModelPort为MockModel。操作产生的数据库、runs、node_modules与dist不进入最终源代码包。

```bash
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pytest -q
python scripts/showcase_demo.py --output runs/clean-dynamic-demo
python scripts/http_smoke.py --output runs/clean-http.json
python scripts/verify_archive.py
(cd frontend && npm ci && npm test && npm run build)
```

发布前最终文档与截图加入导出后再次运行公开内容扫描、链接检查、历史校验及文件清单Hash。测试对应的runtime/前端源码与最终公开源码逐字节对比；文档增补没有改变被测试的业务实现。

GitHub托管CI在用户确认创建仓库后执行；本轮配置已准备，模型凭据不进入CI。
