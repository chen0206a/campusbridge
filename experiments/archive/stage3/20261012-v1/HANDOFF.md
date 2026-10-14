# CampusBridge 第三阶段验收

已交付 React/Vite/TypeScript 学生端和辅导员工作台，并按用户要求将整套界面调整为**靛蓝、雾灰、暖白**。开发环境前后端均已启动；生产构建也能由 Web 后端同源提供，浏览器实际验证完成。

## 验收结果

| 项目 | 实际结果 |
|---|---|
| 后端完整回归 | **124 passed / 0 failures / 0 errors / 0 skipped**，54.58 秒 |
| 原后端 119 项 | 保留并全部通过；本轮新增 Web API 测试 5 项 |
| 前端自动化 | **27 passed / 0 failures / 0 errors**，最终 run 7.84 秒 |
| 类型与构建 | TypeScript `tsc -b`、Vite production build 通过 |
| 浏览器学生链路 | 登录、历史、新建、多轮、真实 HTTP SSE、引用、来源详情、刷新恢复通过 |
| 登录错误 | 错误凭据显示「账号或密码不正确」，不会误报会话过期 |
| 学生隔离 | 学生禾看不到澜的会话；退出清除本项目客户端缓存并撤销当前 token |
| 工单链路 | 实际浏览器完成 **OPEN → CLAIMED → IN_PROGRESS → CLOSED**，版本 0→3，4 条操作历史 |
| 状态筛选 | 已关闭筛选正常显示已完成事项 |
| 生产构建服务 | `/`、JS/CSS assets、Swagger、OpenAPI、health 均 200；浏览器可登录并恢复已关闭事项 |
| 模型模式 | 全部 MockModel，实际浏览器对话 3 轮，**0 外部模型请求** |
| 历史完整性 | **164/164 受保护文件 SHA256 不变**；第二阶段 30 条 frozen packet verify 通过 |

后端有一条沿用的 Starlette TestClient/httpx 弃用提示，完整测试正常。前端初次测试发现关闭弹窗在每次输入时重置焦点；已修复，失败记录及最终通过记录均保留。浏览器发现错误凭据触发全局过期提示的问题，也已修复并补回归。

## 本轮代码

新增 `frontend/` 独立工程，包含组件、样式、类型、请求层、SSE 解析、会话恢复与测试：

- `App.tsx`：登录/退出、服务端身份恢复、按实际角色选择页面。
- `StudentWorkspace.tsx`：会话列表、首轮发送、多轮、逐步显示、引用、超时/断连恢复、移动侧栏。
- `CounselorWorkspace.tsx`：工单列表/筛选、详情、认领、处理、关闭确认、操作历史、冲突与幂等反馈。
- `api.ts`、`sse.ts`、`session.ts`：相对 URL、Authorization、POST streaming、超时、协议校验、请求身份保持。
- `components.tsx`：安全文本、来源弹窗、焦点、加载和业务错误。
- `icons.tsx`、`styles.css`：原创桥形品牌、图标与响应式样式；无外部图片或字体依赖。
- 27 项 Vitest/Testing Library 测试与 npm 锁文件。

后端通过新的 `web_api.py` 包装现有 create_app，新增当前用户、退出、所属会话列表和授权学生表达详情；复用原 SSE、run、RAG 来源与工单 API。`scripts/web_backend.py` 为独立入口。原第二阶段 API、runtime、Agent、Prompt、语料与 scorer 源文件没有改写。

## 实际浏览器演示

1. 学生澜登录，发送图书馆预约问题，收到 SSE 正文与引用。
2. 打开来源，查看原文、original-v2、作者与 MIT；刷新恢复引用，再发送后续问题。
3. 在真实 390×844 iframe 中打开历史侧栏、新建对话，发送「我想找辅导员讨论最近适应校园生活的困难。」；后台创建人工关注事件和工单，学生只看到支持性回复。
4. 退出，用学生禾登录，历史为空；错误密码也验证了业务提示。
5. 辅导员登录，打开 `CB-B6844A`，认领、填写处理记录进入处理中，再填写关闭依据并确认关闭。
6. 最终 CLOSED/version=3；CREATE、CLAIM、PROCESS、CLOSE 全部有记录。
7. 构建后的同源 8015 应用再次登录，恢复已关闭工单。

响应式预览页只将同一真实应用装入固定尺寸 iframe，没有替换数据或组件。学生移动主区宽度/内容宽度均 390；辅导员移动主区均 375（滚动条占位），无横向溢出。桌面 iframe 为 1360×900。关键页面截图共 17 张，见 [截图清单](screenshots/INDEX.md)。

版本冲突、权限拒绝、重复/结果不确定的操作重试、流式断连、异常帧、超时取消与 FAILED 恢复由自动化测试覆盖；浏览器现场展示的是正常 Mock 流程与登录错误。

## 报告与素材

- `backend-tests.xml`：124 项完整结果。
- `frontend-tests.xml`：初次失败记录；`frontend-tests-final.xml`：修复后 24 项；`frontend-tests-final-v2.xml`：最终扩展的 27 项。
- `test-summary.json`：从 JUnit 解析的最终计数。
- `browser-demo-data.json`、`browser-run-01..03.json`：浏览器实际写入数据、运行事件与工单审计核验，全部为虚构数据。
- `production-http-check.json`、`frontend-build.json`：实际同源静态资源与构建 Hash。
- `integrity.json`：164 文件保护与 frozen packet 核验。
- `SOURCE_SHA256SUMS.txt`、`SHA256SUMS.txt`：本轮源码与报告清单。
- [项目展示文档](../../../docs/PROJECT_SHOWCASE.md)：简介、演示顺序和配图。

## 运行

工程根目录：

```powershell
$env:CAMPUSBRIDGE_MODEL_PROVIDER='mock'
.\.venv\Scripts\python.exe -X utf8 scripts/web_backend.py --port 8015 --database data/web-demo/campusbridge.sqlite3
```

前端目录：

```powershell
npm ci
npm run dev
npm test
npm run build
```

开发应用 <http://127.0.0.1:5173/>；同源构建应用 <http://127.0.0.1:8015/>；新 Web 后端 Swagger <http://127.0.0.1:8015/docs>。演示账号 `student_lan`、`student_he`、`counselor_yu`，公开演示密码 `CampusDemo-2026!`。详细说明见 [frontend/README](../../../frontend/README.md)。

前后端服务保留运行，供用户验收；原 8014 服务未停止或替换。本轮工作到此结束，未发布 GitHub、未调用真实模型、未启动训练或新 Agent 架构。
