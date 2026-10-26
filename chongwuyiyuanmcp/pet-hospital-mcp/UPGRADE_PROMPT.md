# UPGRADE_PROMPT.md

本文件是给「后续接手/继续开发」的 AI 或开发者看的实现说明：当前服务怎么搭的、
哪些约定必须遵守、新增工具应该怎么做。

## 已完成阶段（阶段一）

单一 MCP 工具 `list_pets`，适配 Go 宠物医院 REST API `GET /api/v1/pets`，
通过官方 Python SDK 2.x 的 `MCPServer` 以无状态 Streamable HTTP（协议
2026-07-28）对外暴露。

关键约束（阶段一已满足，后续不得回退）：

- `mcp==2.0.0`、Python 3.11+、协议 `2026-07-28`；
- 只用 `mcp.server.MCPServer`，**禁止** `mcp.server.fastmcp.FastMCP`；
- 无状态：不实现旧 `initialize`、不使用 `Mcp-Session-Id`、无会话存储/过期/上限；
- MCP 服务是独立 Python 服务，只能通过 HTTP 调 Go REST API，不修改 Go 后端；
- 上游地址 `PET_HOSPITAL_BASE_URL`，MCP 监听 `MCP_HOST` / `MCP_PORT`；
- 保留 `/health`；教学场景无认证、无 CORS / Origin 校验；
- 工具名 `snake_case`；不新增后端不支持的私有业务参数；
- 统一错误信封 `{error: {code, message, details}}`，错误码见 `errors.py`；
- 上游调用必须有超时和有限重试，禁止把 HTTPX / Pydantic / SDK / Python 堆栈
  原样暴露给客户端；
- 日志至少含 `timestamp` / `tool_name` / `params` / `status` / `duration_ms`，
  `ownerPhone` / `ownerAddr` / `chipNo`（及 snake_case 写法）递归脱敏；
- 测试不得访问真实 Go 服务（用 `httpx.MockTransport` / `ASGITransport`）。

## 架构速览

```text
AI Agent (MCP client)
   │  2026-07-28 Streamable HTTP (POST /mcp)
   ▼
pet_hospital_mcp (Python, uvicorn + Starlette)
   ├── server.py            MCPServer 装配、/health、lifespan 中持有 REST 客户端
   ├── rest_client.py       httpx 客户端：超时 / 有限重试 / 错误码映射 / 信封解析
   ├── tools/list_pets.py   Pydantic 严格校验 + 调用上游 + 输出模型校验 + 日志
   ├── errors.py            统一错误码与 ToolFailure
   └── logging_config.py    JSON 日志 + 递归脱敏
   │  HTTP (GET /api/v1/pets)
   ▼
Go 宠物医院 REST API（唯一业务后端，禁止修改）
```

工具闭包不直接持有客户端，而是通过 `client_provider`（`SimpleNamespace`）从
lifespan 中取 `PetHospitalClient`，便于测试注入 `MockTransport`。

## 新增工具的流程（阶段二及以后）

以新增 `get_pet`（`GET /api/v1/pets/{id}`）为例：

1. 在 `src/pet_hospital_mcp/tools/` 新增 `get_pet.py`；
2. `rest_client.py` 增加对应方法（复用超时/重试/信封解析与错误映射）；
3. 输入模型 `extra="forbid"` + `strict=True`，枚举与范围对齐
   `GET /api/v1/meta` 的真实允许值；
4. 成功输出模型对应 Go 信封中的 `data`，`extra="ignore"`；
5. 用 `_failure_result` / `log_tool_event` 保持统一错误信封与日志字段；
6. 在 `server.py` 的 `create_app` 中调用 `get_pet_module.register(mcp, client_provider)`；
7. 在 `tests/` 增加覆盖（参数转发、校验失败、4xx/5xx、超时、非法响应、
   工具注册与 HTTP 调用），保持 `pytest -q` 全绿。

## 测试约定

- 运行命令固定：`cd pet_hospital_mcp && pytest -q`；
- 测试夹具集中在 `tests/conftest.py`：
  - `_LifespanRunner` 用同一个 asyncio 任务进入/退出 ASGI lifespan
    （SDK 内部 anyio cancel scope 要求同任务进出，pytest-asyncio 的异步
    fixture setup/teardown 跨任务会触发 cancel scope 异常）；
  - `make_mcp_client(handler)` 注入 `httpx.MockTransport` 并返回 ASGI 客户端；
  - `post_jsonrpc` 为 2026-07-28 请求附加 `_meta` 信封与 `MCP-Protocol-Version`、
    `Mcp-Method` 头；
- HTTP 客户端 base_url 必须使用 `http://127.0.0.1:8000` 这类 SDK DNS 重绑定
  保护允许的本地地址（Host 头校验）。

## 阶段一已知取舍

- `list_pets` 的函数签名保留 14 个 `Any` 参数仅为生成稳定签名，真正校验走
  `ctx._input_params` 里的原始 arguments，以便拒绝未知字段、NaN/Infinity 与
  类型错误（SDK 生成的参数模型默认忽略额外字段）；
- 注册后用 `registered.parameters = ListPetsInput.model_json_schema()` 覆盖
  对外 JSON Schema，保证严格校验规则对客户端可见；
- 未注册工具在 SDK 2.x 下返回 `isError=true` 的工具结果（text 为
  "Unknown tool: ..."），这是官方行为，测试按此断言。

## 阶段二（未实现，明确不做）

当前只实现 `list_pets`，未实现任何阶段二工具。后续如需新增，按上方
「新增工具的流程」执行，并在 README 中同步更新工具清单。
