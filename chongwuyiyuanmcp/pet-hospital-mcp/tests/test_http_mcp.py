"""通过 HTTP 验证 MCP 2026-07-28 无状态连接流程、工具发现与调用。

覆盖场景 2、6、7：
- server/discover 发现协议版本与能力；
- tools/list 验证工具名与 JSON Schema；
- tools/call 成功调用 list_pets（经 /mcp 打 Go 后端，上游由 MockTransport 拦截）；
- 旧 initialize 不存在、全程无 Mcp-Session-Id；
- /health 健康检查；
- 输入校验失败与上游不可达都返回统一错误信封。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from conftest import (
    post_jsonrpc,
    sample_envelope,
    sample_pet,
)
from mcp_types.version import LATEST_PROTOCOL_VERSION as PROTOCOL_VERSION

from pet_hospital_mcp.server import HEALTH_PATH, MCP_ENDPOINT_PATH


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers.get("content-type", "").startswith("application/json")
    body = response.json()
    assert "error" not in body, body
    return body["result"]


async def test_health_endpoint(make_mcp_client: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # 健康检查不应触达上游
        raise AssertionError("health must not call upstream")

    client = await make_mcp_client(handler)
    response = await client.get(HEALTH_PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["protocolVersion"] == PROTOCOL_VERSION
    assert payload["mcpEndpoint"] == MCP_ENDPOINT_PATH
    assert payload["backend"]["base_url"] == "http://backend.test"


async def test_discover_reports_protocol_and_tools_capability(make_mcp_client: Any) -> None:
    """场景 7：2026-07-28 用 server/discover 发现服务，不经过 initialize。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    response = await post_jsonrpc(client, "server/discover")
    result = _result(response)

    assert PROTOCOL_VERSION in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    # 2026-07-28 把 serverInfo 放在结果 _meta 中
    server_info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert server_info["name"] == "pet-hospital-mcp"
    # 无状态 JSON 响应不应下发会话 ID
    assert response.headers.get("mcp-session-id") is None


async def test_initialize_is_not_supported(make_mcp_client: Any) -> None:
    """场景 7：旧协议 initialize 方法在 2026-07-28 端点上不存在。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    response = await post_jsonrpc(
        client,
        "initialize",
        params={"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "old"}},
    )
    # 未知方法：JSON-RPC -32601，经 HTTP 映射为 404
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == -32601
    assert "initialize" in json.dumps(body, ensure_ascii=False)


async def test_request_without_envelope_meta_is_rejected(make_mcp_client: Any) -> None:
    """2026-07-28 无状态请求必须携带协议信封 _meta。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    response = await post_jsonrpc(client, "tools/list", include_meta=False)
    assert response.status_code == 400
    # SDK 2.x 对缺失信封 _meta 报 Invalid params（-32602）
    assert response.json()["error"]["code"] == -32602


async def test_tools_list_exposes_list_pets_schema(make_mcp_client: Any) -> None:
    """场景 6：工具注册、工具名（snake_case）与严格 JSON Schema。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    response = await post_jsonrpc(client, "tools/list")
    result = _result(response)
    tools = result["tools"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "list_pets"
    assert tool["annotations"]  # title / readOnlyHint 等注解存在

    schema = tool["inputSchema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "q",
        "name",
        "ownerName",
        "ownerPhone",
        "species",
        "doctor",
        "disease",
        "status",
        "min",
        "max",
        "sortBy",
        "order",
        "page",
        "pageSize",
    }
    # 枚举与范围约束进入对外 schema
    assert set(schema["properties"]["species"]["anyOf"][0]["enum"]) == {
        "犬",
        "猫",
        "兔",
        "鸟",
        "仓鼠",
        "爬宠",
        "其他",
    }
    assert schema["properties"]["page"]["anyOf"][0]["minimum"] == 1
    page_size = schema["properties"]["pageSize"]["anyOf"][0]
    assert page_size["minimum"] == 1
    assert page_size["maximum"] == 500
    assert schema["properties"]["min"]["anyOf"][0]["minimum"] == 0

    # 描述包含用途、参数、适用场景、返回值
    description = tool["description"]
    for keyword in ("用途", "适用场景", "参数", "返回值"):
        assert keyword in description


async def test_call_list_pets_success_over_http(make_mcp_client: Any) -> None:
    """场景 1 + 7：不经过 initialize、无会话 ID，直接 tools/call 成功调用。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.raw_path.decode()
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=sample_envelope(
                items=[
                    sample_pet(
                        "P001",
                        records=None,
                        charges=None,
                    ),
                    sample_pet(
                        "P002",
                        name="咪咪",
                        species="猫",
                        records=[{"id": "R1", "diagnosis": "猫藓", "prescription": None}],
                        charges=[{"id": "C1", "item": "药浴", "amount": 300.0}],
                    ),
                ],
                total=2,
                total_cost=1580.5,
            ),
        )

    client = await make_mcp_client(handler)
    arguments = {
        "species": "犬",
        "min": 1000,
        "sortBy": "totalCost",
        "order": "desc",
        "page": 1,
        "pageSize": 20,
    }
    response = await post_jsonrpc(
        client,
        "tools/call",
        params={"name": "list_pets", "arguments": arguments},
    )
    result = _result(response)
    assert result["isError"] is False
    assert response.headers.get("mcp-session-id") is None

    # 上游确实收到了正确的路径与参数
    assert seen["path"].startswith("/api/v1/pets")
    assert seen["params"]["species"] == "犬"
    assert seen["params"]["min"] == "1000.0"
    assert seen["params"]["sortBy"] == "totalCost"
    assert seen["params"]["order"] == "desc"
    assert seen["params"]["page"] == "1"
    assert seen["params"]["pageSize"] == "20"

    data = result["structuredContent"]
    assert data["total"] == 2
    assert data["totalCost"] == 1580.5
    assert [pet["id"] for pet in data["items"]] == ["P001", "P002"]
    # null 与数组两种表现都被规整
    assert data["items"][0]["records"] == []
    assert data["items"][0]["charges"] == []
    assert data["items"][1]["records"][0]["prescription"] == []
    assert data["items"][1]["charges"][0]["item"] == "药浴"

    # 文本内容是同一份 JSON
    text_blocks = [block for block in result["content"] if block["type"] == "text"]
    assert text_blocks and json.loads(text_blocks[0]["text"]) == data


async def test_call_list_pets_validation_error(make_mcp_client: Any) -> None:
    """场景 2：非法输入返回统一错误信封，且不会触达上游。"""
    upstream_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=sample_envelope())

    client = await make_mcp_client(handler)

    response = await post_jsonrpc(
        client,
        "tools/call",
        params={"name": "list_pets", "arguments": {"page": 0, "species": "恐龙", "hack": 1}},
    )
    result = _result(response)
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "page" in error["message"]
    assert "恐龙" in error["message"]
    assert "hack" in error["message"]
    assert error["details"]["issues"]
    assert upstream_calls == 0  # 校验失败不允许打到上游


async def test_call_list_pets_backend_unavailable(make_mcp_client: Any) -> None:
    """场景 4：连接异常经 HTTP 工具调用返回 BACKEND_UNAVAILABLE 统一信封。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = await make_mcp_client(handler)
    response = await post_jsonrpc(
        client,
        "tools/call",
        params={"name": "list_pets", "arguments": {"q": "金毛"}},
    )
    result = _result(response)
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "BACKEND_UNAVAILABLE"
    # 内部异常文本不泄露给 MCP 客户端
    assert "connection refused" not in json.dumps(error, ensure_ascii=False)


async def test_two_independent_stateless_calls(make_mcp_client: Any) -> None:
    """场景 7：连续两次独立调用，无 initialize、无会话，均成功。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    for request_id in (1, 2):
        response = await post_jsonrpc(
            client,
            "tools/call",
            params={"name": "list_pets", "arguments": {"q": str(request_id)}},
            request_id=request_id,
        )
        result = _result(response)
        assert result["isError"] is False
        assert response.headers.get("mcp-session-id") is None


async def test_unknown_tool_returns_is_error_result(make_mcp_client: Any) -> None:
    """SDK 2.x 对未注册工具的 tools/call 返回 isError=true 的结果而非 JSON-RPC 错误。"""
    client = await make_mcp_client(lambda request: httpx.Response(200, json=sample_envelope()))

    response = await post_jsonrpc(
        client,
        "tools/call",
        params={"name": "create_pet", "arguments": {}},
    )
    result = _result(response)
    assert result["isError"] is True
    texts = [block["text"] for block in result["content"] if block["type"] == "text"]
    assert any("create_pet" in text for text in texts)
    assert response.headers.get("mcp-session-id") is None
