# 企业文档AI向量管理系统 (AnythingLLMMCP)

基于 MCP（Model Context Protocol）协议的企业文档向量管理系统。通过 MCP 对接 AnythingLLM，提供文档上传、向量化、语义检索与对话工具，让 AI 助手能管理企业知识库。

## 架构

```
AI 客户端 ──stdio──> AnythingLLMMCP (本项目) ──HTTP──> AnythingLLM Server (已安装于 D:\anythingllm)
```

- **AnythingLLMServer**：即已安装的 AnythingLLM Desktop（`D:\anythingllm`），作为后端服务，提供文档解析、向量化、语义检索、LLM 对话能力
- **AnythingLLMMCP**：本项目，MCP server，把 AnythingLLM 的 Developer API 包装为 MCP 工具

## 功能

- 工作区管理（列出 / 创建）
- 文档上传（本地文件 / 网页链接）并自动向量化
- 工作区文档列表
- 语义搜索（向量检索，返回匹配片段）
- 对话问答（向量检索 + LLM 生成）

## 环境要求

- Python 3.11+
- AnythingLLM Desktop 已安装并运行（默认监听 `http://localhost:3001`）

## 安装

```powershell
cd D:\AnythingLLMMCP
pip install -e .
```

## 配置

1. 启动 AnythingLLM Desktop
2. 在 UI 中进入「设置 → API」，生成 Developer API 密钥
3. 复制 `.env.example` 为 `.env`，填入密钥：

```powershell
copy .env.example .env
# 编辑 .env，设置 ANYTHINGLLM_API_KEY
```

或在启动前设置环境变量：

```powershell
$env:ANYTHINGLLM_API_KEY = "你的密钥"
```

## 启动 MCP Server

```powershell
python -m anythingllm_mcp.server
```

Server 以 stdio 传输运行，供 MCP 客户端接入。

## MCP 工具一览

| 工具 | 参数 | 功能 |
|---|---|---|
| `verify_auth` | - | 验证 API 密钥 |
| `list_workspaces` | - | 列出所有工作区 |
| `create_workspace` | name, slug? | 创建工作区 |
| `upload_document` | file_path, workspaces? | 上传本地文档并向量化 |
| `upload_link` | url, workspaces? | 上传网页链接并向量化 |
| `get_workspace_documents` | slug | 列出工作区文档 |
| `chat_with_workspace` | slug, message | 向量检索 + LLM 对话 |
| `search_workspace` | slug, query | 语义搜索（不走 LLM） |

## 测试

```powershell
pytest tests/ -v
```

测试使用 mock，不依赖真实 AnythingLLM 服务。

## 在 MCP 客户端中接入

以 Claude Desktop 的 `claude_desktop_config.json` 为例：

```json
{
  "mcpServers": {
    "anythingllm": {
      "command": "python",
      "args": ["-m", "anythingllm_mcp.server"],
      "cwd": "D:\\AnythingLLMMCP",
      "env": {
        "ANYTHINGLLM_API_BASE_URL": "http://localhost:3001",
        "ANYTHINGLLM_API_KEY": "你的密钥"
      }
    }
  }
}
```

## 架构说明

- **无状态**：每次工具调用独立处理，不跨调用保持会话
- **异步**：基于 httpx 异步客户端，适配 IO 密集的 API 调用
- **错误处理**：工具内部捕获异常，返回 `{"ok": false, "error": ..., "detail": ...}` 结构化错误
- **认证**：所有请求通过 `Authorization: Bearer <key>` 头认证
