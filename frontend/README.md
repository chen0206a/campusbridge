# CampusBridge Web

React + Vite + TypeScript 原创 Web 应用，采用米白、炭黑和砖红配色。学生空间提供持久化对话、审核后 SSE、资料引用和刷新恢复；辅导员工作台提供工单认领、处理、关闭与操作历史。

## 启动

Node.js 22.12+，Python 环境沿用工程 `.venv`。先启动 Web 后端（与原 8014 API 入口独立）：

```powershell
# 在工程根目录执行
$env:CAMPUSBRIDGE_MODEL_PROVIDER='mock'
.\.venv\Scripts\python.exe -X utf8 scripts/web_backend.py --port 8015 --database data/web-demo/campusbridge.sqlite3
```

另开终端启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

浏览器访问 <http://127.0.0.1:5173/>。本机默认 npm 缓存有权限问题时使用：

```powershell
npm ci --cache ../runs/npm-cache
```

浏览器代码使用相对 `/api` 请求。Vite 开发代理通过 `.env.local` 或进程环境变量 `CAMPUSBRIDGE_PROXY_TARGET` 配置；默认目标仅用于本地 8015。该变量属于开发服务器，不会作为 API key 打入浏览器包。[Vite 代理配置](https://vite.dev/config/server-options#server-proxy)

## 生产构建本地演示

```powershell
npm run build
```

构建完成后，Web 后端可在 <http://127.0.0.1:8015/> 同源服务 `dist` 与 API；首次构建发生在后端启动之后时，重启该 Web 后端以挂载静态目录。也可 `npm run preview` 检查构建（使用同一开发代理）。本轮按 loopback、单进程演示运行。

## 演示账号与流程

演示密码：`CampusDemo-2026!`。`student_lan`、`student_he` 为虚构学生，`counselor_yu` 为辅导员。登录页的「填入演示账号」只填写公开演示凭据；业务角色来自服务端 `/auth/me`。

1. 学生澜登录，点击「了解校园事务」，发送图书馆预约问题。
2. 点击回复下方引用，查看原文、版本、作者与 MIT 许可。
3. 刷新页面，历史和引用恢复；继续询问预约步骤。
4. 新建对话，输入「我想找辅导员讨论最近适应校园生活的困难。」。
5. 退出后用学生禾登录，确认看不到澜的历史会话。
6. 用辅导员登录，选择工单，认领、填写处理意见、保存进入处理中、填写关闭依据并确认关闭。
7. 查看四条操作历史与已关闭状态。

## 接口与状态

复用原 SSE、run、知识来源和工单契约。`web_api.py` 只新增 GET `/auth/me`、POST `/auth/logout`、GET `/conversations`、GET `/cases/{id}/student-context`，与根路径静态挂载。编排增强复用同一 Web API 契约。

认证使用内存状态和同标签页 `sessionStorage`，刷新时由服务端再次验证。密码不保存；退出撤销服务端当前 token，并清除本项目缓存。学生只读取自己的会话；学生表达详情仅授权同校园辅导员读取。

fetch streaming 支持 POST、Authorization 与 Idempotency-Key。解析器处理 UTF-8 跨块、CRLF、多行 data、错误帧和未完整结束；只有收到 done 并读取已提交结果后清除 pending。读取的是真实 HTTP SSE，显示已审核正文。[ReadableStream 说明](https://developer.mozilla.org/en-US/docs/Web/API/ReadableStream)

中断时保存原请求身份，重试优先 GET 原 run 的 stream，meta 前中断则复用原 POST 幂等键。REST 等待上限 20 秒、流式连接 100 秒；超时保留恢复入口。FAILED 终态可查看已保存历史再明确发送新消息。发送、建会话和工单操作有并发点击保护。

工单操作携带服务端版本；版本冲突自动拉取最新详情并保留处理意见。传输结果不确定时重用原操作键，服务端幂等重放。权限拒绝与重复键冲突显示业务提示。React 转义所有文本，来源只使用已提交的 citations，弹窗支持 Esc、焦点恢复与 Tab 限制。

## 测试

```powershell
npm test
npm run build
```

31 项自动化测试：流式分块/协议与失败、登录/会话、引用详情、学生刷新和首轮提交、工单全流程、权限拒绝、冲突与幂等重试。后端完整回归命令与最新结果在根 README 和干净复现报告。

响应式验收页 <http://127.0.0.1:5173/responsive-preview.html> 在真实 390×844 iframe 内运行同一应用；`?size=desktop` 使用 1360×900。它是开发验收工具，产品菜单不链接它。界面截图见 `docs/PROJECT_SHOWCASE.md`，历史数据副本见 `experiments/archive/stage3/`。

## 核心文件

```text
src/
  App.tsx                 登录、服务端身份验证、角色切换、退出
  StudentWorkspace.tsx    历史、多轮、SSE、刷新、重试和移动侧栏
  CounselorWorkspace.tsx 工单筛选、认领、意见、关闭、历史与冲突
  api.ts / sse.ts         相对 API、错误映射、超时、流式协议
  session.ts / types.ts   同标签页恢复、类型契约
  components.tsx         引用详情、安全正文、加载与弹窗
  icons.tsx / styles.css  原创图标、品牌与响应式样式
  test/                  Vitest + Testing Library
```

新增代码与图标为本工程原创 MIT；依赖版本与上游许可信息在 package-lock.json 中保留。公开仓库中的发布版本保持不变；本轮 UI 更新在独立分支验收。


## 第一版视觉与交互（历史记录，2026-10-16）

在 `design/frontend-refresh-v1` 分支开发。深靛蓝导航、暖白内容区，辅以琥珀/紫/玫瑰色状态；登录页采用原创 SVG 校园插画。新增四类话题入口、话题筛选、三步支持说明、输入字数、宽屏对话资料汇总。

辅导员工作台增加当前工单数据汇总、工单/学生 ID 搜索、负责人筛选和重置筛选。所有数字来自当前服务端返回的数据；工单状态、权限和处理操作沿用原 API。

宽屏验收可使用 `/responsive-preview.html?size=desktop&fit=1`：同一应用在 1360×900 iframe 中运行，按外层窗口缩放，便于较小显示器完整检查三栏布局。移动验收仍使用默认 390×844 入口。

详见 [前端更新验收记录](../docs/FRONTEND_REFRESH_V1.md)。


## 当前界面：服务台风格 v2

本地分支 `design/frontend-editorial-v2`。浅色导航、砖红操作入口、分隔线和列表式话题入口，替换上一版深蓝侧栏与卡片布局。登录页使用功能目录，不再展示宣传插画。

界面文字使用功能名称和操作说明，例如“新对话”“工单管理”“参考资料”“认领后可填写处理记录”。对话、引用、筛选、工单处理和权限契约保持原样。

见 [当前界面截图与验收](../docs/FRONTEND_STYLE_V2.md)。本地预览仍为 <http://127.0.0.1:5173/>。
