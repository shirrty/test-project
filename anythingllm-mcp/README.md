# llmmcp

本项目是一个基于 [MCP](https://modelcontextprotocol.io) 的 AnythingLLM 桥接服务：通过 Streamable HTTP 对外暴露 `ask_workspace` 工具，向本地 AnythingLLM workspace 提问并返回 AI 回答。

## 目录结构

| 文件 | 说明 |
| --- | --- |
| `server.py` | MCP 服务器（FastMCP 风格），导出 `app`（Streamable HTTP ASGI 应用） |
| `test_client.py` | 最小客户端，用于验证服务可用 |
| `requirements.txt` | Python 依赖 |
| `.env.example` | 环境变量示例（复制为 `.env` 后填入真实 API Key） |
| `opencode.json` | opencode 项目级 MCP 配置 |
| `uvicorn.log` / `uvicorn.err.log` | uvicorn 运行日志（已 gitignore，不入库） |

## opencode 项目级 MCP

本项目已在 `opencode.json` 中配置了项目级 MCP（仅当前项目生效，不影响全局）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "anythingllm": {
      "type": "remote",
      "url": "http://127.0.0.1:7000/mcp",
      "enabled": true
    }
  }
}
```

opencode 通过 `type: "remote"` 连接已运行的 MCP 服务，因此**必须先启动本服务器**，MCP 工具才会出现在 opencode 中。

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 配置 API Key（必填）

服务不再内置任何密钥，必须通过环境变量提供 `ANYTHINGLLM_API_KEY`，
否则启动时会直接报错退出。

1. 在 AnythingLLM 界面打开 **Settings → API Keys → New API Key** 生成密钥；
2. 复制 `.env.example` 为 `.env` 并填入密钥（`.env` 已被 gitignore 忽略）；
3. 或在启动前直接设置环境变量。

```powershell
$env:ANYTHINGLLM_API_KEY = "<你的 API Key>"
```

其余环境变量均有默认值，可按需覆盖：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ANYTHINGLLM_API_KEY` | 无（**必填**） | AnythingLLM API Key |
| `ANYTHINGLLM_URL` | `http://localhost:3001` | AnythingLLM 服务地址 |
| `ANYTHINGLLM_WORKSPACE` | `ai` | 目标 workspace slug |

## 启动 MCP 服务器

```powershell
python -m uvicorn server:app --host 127.0.0.1 --port 7000
```

- 使用 `--reload` 可开启代码热重载（改动 `server.py` 自动重启）。
- 启动后，MCP 端点地址为 `http://127.0.0.1:7000/mcp`。

## 停止 MCP 服务器

前台运行时按 `Ctrl+C` 即可停止。

若服务器在后台运行（或需要按端口强制停止）：

```powershell
# 1. 找到占用 7000 端口的进程 PID
Get-NetTCPConnection -LocalPort 7000 -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess

# 2. 结束该进程（把 <PID> 换成上一步查到的进程号）
Stop-Process -Id <PID>
```

或使用 `taskkill`：

```powershell
taskkill /PID <PID> /F
```

## 验证服务是否可用

```powershell
python test_client.py
```

预期输出（TOOLS 列表包含 `ask_workspace`，IS_ERROR 为 False）：

```
TOOLS: [('ask_workspace', None)]
IS_ERROR: False
```