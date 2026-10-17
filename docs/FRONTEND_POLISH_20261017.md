# Frontend polish · 2026-10-17

## 界面改动

白底和橙色操作按钮延续现有风格，参考 Apple 网站的层级、留白和过渡节奏。实现使用项目自身的 CSS 和已有组件。

- 系统字体栈，调整标题、正文、卡片与工作区间距。
- 统一圆角、细边框、柔和阴影；顶栏、输入区及弹窗背景使用轻微模糊。
- 卡片悬停、按钮按下、面板和弹窗进入使用短过渡。悬停位移仅适用于支持精确指针的设备。
- `prefers-reduced-motion: reduce` 关闭动画、过渡和位移。
- 保留响应式断点、学生/辅导员功能、直接的界面文案和服务端权限校验。

主要实现位于 `frontend/src/refresh.css`。本次未增加依赖或外部图片，也未修改 Agent、Prompt、检索、工单、SSE 和模型配置。

## 实际验证

| 检查 | 结果 |
|---|---|
| 前端 Vitest | 31 passed / 0 failed |
| TypeScript + Vite 生产构建 | passed |
| Web API、发布及 RC 定向后端回归 | 21 passed / 0 failed |
| 浏览器：登录、历史恢复、知识引用详情 | passed |
| 浏览器：辅导员工单详情与四条操作历史 | passed |

```bash
npm --prefix frontend test -- --reporter=dot
npm --prefix frontend run build
python -X utf8 -m pytest tests/test_web_api.py tests/test_release.py tests/test_release_candidate.py -q
```

浏览器使用现有 MockModel + dynamic 演示服务及虚构账号。截图包括登录、空会话、历史对话、来源弹窗和已关闭工单；未调用收费模型。减少动态效果由 CSS 媒体查询实现，本次未切换操作系统设置单独验收该分支。

## 新截图

[展示页](PROJECT_SHOWCASE.md) · [图片 manifest](assets/showcase-20261017/manifest.json)

原始浏览器 JPEG 字节直接保存，未缩放、修图或替换内容。工单图采用全页捕获，保留完整操作历史。中英文 README 已切换至新截图；旧截图、Stage 1–6 与 RAG/工程实验保留原始文件。

## 发布

本地验证后推送当前 main，并通过 GitHub Actions 继续执行完整后端矩阵、前端与真实 Redis 回归。远程运行结果以该提交的 Actions 状态为准。v1.0.0 标签及历史 Release 保留原指向。
