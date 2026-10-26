# pet-hospital-mcp

无状态 MCP（Model Context Protocol）适配服务，用官方 Python SDK 2.x 的
**`MCPServer`** 把现有 **Go 宠物医院 REST API** 暴露给 AI Agent。

- 协议版本：**2026-07-28**（Streamable HTTP，无状态）
- SDK：**`mcp==2.0.0`**（官方 Python SDK 2.x，非 FastMCP）
- 服务形态：独立 Python 服务，只通过 HTTP 调用 Go REST API，**不修改 Go 后端**

## 目录结构

```text
pet_hospital_mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── server.py
│       ├── rest_client.py
│       ├── errors.py
│       ├── logging_config.py
│       └── tools/
│           ├── __init__.py
│           └── list_pets.py
└── tests/
    ├── conftest.py
    ├── test_http_mcp.py
    ├── test_list_pets_models.py
    └── test_rest_client.py
```

## 环境要求

- Python 3.11+
- Go 宠物医院服务已启动（默认 `http://127.0.0.1:8080`）

## 安装

```bash
cd pet_hospital_mcp
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

## 启动 Go REST API（必须先启动）

```bash
cd pet-hospital-windows-amd64   # 发行包根目录
pethospital.exe                 # 默认监听 127.0.0.1:8080
```

启动后确认：

```bash
curl http://127.0.0.1:8080/health
```

## 启动 MCP 服务

```bash
cd pet_hospital_mcp
pet-hospital-mcp                 # 或 python -m pet_hospital_mcp
```

默认监听 `127.0.0.1:8000`，仅本机可访问。

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | MCP HTTP 监听地址 |
| `MCP_PORT` | `8000` | MCP HTTP 监听端口 |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | Go REST API 地址 |
| `PET_HOSPITAL_TIMEOUT` | `5.0` | 单次上游请求超时（秒） |
| `PET_HOSPITAL_MAX_RETRIES` | `1` | 上游失败后的重试次数（不含首次） |
| `MCP_LOG_LEVEL` | `INFO` | 日志级别 |

示例：

```bash
MCP_HOST=127.0.0.1 MCP_PORT=8001 PET_HOSPITAL_BASE_URL=http://127.0.0.1:8080 pet-hospital-mcp
```

## MCP 端点

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/mcp` | POST | 无状态 Streamable HTTP JSON-RPC（协议 2026-07-28） |
| `/health` | GET | MCP 服务自身健康检查 |

`/health` 示例：

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "service": "pet-hospital-mcp",
  "version": "1.0.0",
  "protocolVersion": "2026-07-28",
  "timestamp": "2026-09-17T00:00:00+00:00",
  "mcpEndpoint": "/mcp",
  "backend": { "base_url": "http://127.0.0.1:8080" }
}
```

## 无状态说明

- **不实现**旧协议的 `initialize` 握手；
- **不使用** `Mcp-Session-Id`，无会话存储、无会话过期、无 `max_sessions`；
- 每个请求独立携带协议信封 `_meta`（`protocolVersion` / `clientCapabilities` / `clientInfo`）；
- 发现方式为 2026-07-28 规定的 `server/discover`，工具调用为 `tools/call`。

## 已实现的工具

### `list_pets`

适配 `GET /api/v1/pets`，只暴露 Go 后端真实支持的 14 个查询参数：
`q`、`name`、`ownerName`、`ownerPhone`、`species`、`doctor`、`disease`、
`status`、`min`、`max`、`sortBy`、`order`、`page`、`pageSize`。

调用示例（SDK 2.x 客户端视角，`tools/call`）：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_pets",
    "arguments": {
      "species": "犬",
      "min": 1000,
      "sortBy": "totalCost",
      "order": "desc",
      "page": 1,
      "pageSize": 20
    }
  }
}
```

成功返回 Go 信封中的 `data`：`items`、`total`、`page`、`pageSize`、
`totalPages`、`totalCost`；`records` / `charges` 兼容 Go 端 `null` 或数组两种表现。

失败统一返回：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "可读错误信息",
    "details": {}
  }
}
```

错误码：`VALIDATION_ERROR`、`BACKEND_TIMEOUT`、`BACKEND_UNAVAILABLE`、
`BACKEND_API_ERROR`、`BACKEND_INVALID_RESPONSE`、`INTERNAL_ERROR`。

## 验证步骤

### 1. MCP Inspector（官方调试器）

```bash
npx @modelcontextprotocol/inspector
```

Transport 选 **Streamable HTTP**，URL 填 `http://127.0.0.1:8000/mcp`。
进入后：

1. 工具列表应显示 `list_pets`（snake_case）；
2. 输入 `species=犬`、`pageSize=10` 调用，应返回 Go 后端真实数据；
3. 切换协议版本应能确认是 2026-07-28 无状态流程（无会话初始化步骤）。

### 2. SDK 2.x 客户端（Python）

```python
from mcp.client.http import http_client
from mcp import ClientSession

async with http_client("http://127.0.0.1:8000/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        tools = await session.list_tools()
        print([t.name for t in tools.tools])          # ['list_pets']
        res = await session.call_tool("list_pets", {"q": "金毛", "pageSize": 5})
        print(res)
```

### 3. 原生 HTTP（curl，无状态直连）

```bash
# 发现
curl -s http://127.0.0.1:8000/mcp \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"}}}}'

# 调用
curl -s http://127.0.0.1:8000/mcp \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","pageSize":5},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"}}}}'
```

全程响应中不应出现 `Mcp-Session-Id` 头，也不需要先发送 `initialize`。

## 单元测试

测试全部走 `httpx.MockTransport` / `httpx.ASGITransport`，**不会访问真实 Go 服务**。

```bash
cd pet_hospital_mcp
pytest -q
```

预期结果：`53 passed`（覆盖参数转发、输入校验、4xx/5xx、超时与连接异常、
非法响应、工具注册与 JSON Schema、2026-07-28 无状态 HTTP 全流程）。

## 说明

- 教学场景：无认证、无权限、无 CORS / Origin 校验，仅限本机使用；
- 本阶段只实现了 `list_pets` 一个工具，未实现阶段二工具；
- 敏感字段（`ownerPhone`、`ownerAddr`、`chipNo` 及 snake_case 写法）在日志中递归脱敏。
