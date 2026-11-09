# AnythingLLMServer —— AnythingLLM 的网页上传前端

单文件前端，用来把本地文件传进 AnythingLLM 的指定工作区并触发向量化。
纯静态页面，不依赖 Node、不需要构建，双击 `index.html` 即可用。

## 目录

| 文件 | 说明 |
|---|---|
| `index.html` | 全部功能：连接设置、工作区加载、文件上传、状态反馈 |

## 使用前准备

1. 本机已启动 AnythingLLM（默认 `http://localhost:3001`）。
2. 在 AnythingLLM 的「设置 → 工具 → API Keys」里生成一个 Key，并给它写入权限。
3. 至少建好一个工作区（Workspace）。

## 启动

```powershell
# 方式一：直接双击 index.html（推荐，file:// 也能跑）
# 方式二：起个静态服务器，避免个别浏览器对 file:// 的 fetch 限制
cd D:\LLM\AnythingLLMServer
python -m http.server 8090
# 然后浏览器打开 http://localhost:8090/index.html
```

> 如果 AnythingLLM 与页面不同源，需要在 AnythingLLM 的 `.env` 里
> 设置 `CORS_ALLOWED_ORIGINS=http://localhost:8090`（或 `*`，仅本地调试时用）。

## 操作步骤

1. **API 地址**：默认 `http://localhost:3001/api`，按实际部署改。
2. **API Key**：粘贴后点「保存连接设置」，只写进本机浏览器 `localStorage`，
   刷新页面自动回填；换机器或分享电脑前点「清除已存密钥」。
3. **目标工作区**：点「加载工作区」，列表来自 `GET /v1/workspaces`。
4. **选择文件** → 点「上传并向量化」，走 `POST /v1/document/upload`（`addToWorkspaces=<slug>`）。

## 状态与错误

页面把每次请求的结果分四态展示，不会只给一句"成功/失败"：

| 状态 | 含义 |
|---|---|
| 加载中（蓝点 + 文案） | 请求进行中，按钮已禁用，避免重复提交 |
| 成功（绿） | HTTP 200 且 `success !== false`，会列出文档标题、词数、token 估算 |
| 失败（红） | 带上 `HTTP <状态码>：<服务端 error/message 原文>`，最多 300 字 |
| 提示（黄） | 非错误但需要注意，例如工作区列表为空、密钥已清除 |

常见失败对照：

- `HTTP 401`：Key 错或已被吊销 → 重新生成。
- `HTTP 403`：Key 没有该工作区写入权限。
- `HTTP 404`：地址端口不对，或 `/api` 后缀漏了。
- `Failed to fetch`：AnythingLLM 没启动，或跨源被拦（见上面 CORS 说明）。

## 安全说明

- 页面里**没有任何硬编码密钥**；Key 只存在于输入框与浏览器 `localStorage`。
- 所有渲染都用 `textContent` / `createElement`，服务端返回的标题不会当 HTML 执行（无 XSS）。
- 传出去的只有你选中的文件与 `Bearer` 头，没有额外上报。
- 如果曾经把 Key 明文写进代码并提交过，请到 AnythingLLM 后台**吊销后重新生成**——
  删文件不会让历史提交里的 Key 失效。
