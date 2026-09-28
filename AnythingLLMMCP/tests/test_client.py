"""AnythingLLM 客户端单元测试

使用 mock 验证 client 的请求构造逻辑，不依赖真实 AnythingLLM 服务。
运行: pytest tests/test_client.py -v
"""

import asyncio
import os
from unittest.mock import AsyncMock, patch

# 测试前设置环境变量
os.environ["ANYTHINGLLM_API_KEY"] = "test-key"
os.environ["ANYTHINGLLM_API_BASE_URL"] = "http://localhost:9999"

from anythingllm_mcp import client  # noqa: E402


def _test_coro(coro):
    """运行协程并返回结果"""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_headers_contains_auth():
    """请求头应包含 Bearer token"""
    # 重新导入 config 以读取新环境变量
    import importlib
    from anythingllm_mcp import config
    importlib.reload(config)
    importlib.reload(client)
    headers = client._headers()
    assert headers["Authorization"] == "Bearer test-key"


def test_verify_auth_calls_correct_path():
    """verify_auth 应请求 /api/v1/auth"""
    with patch.object(client, "_request", new=AsyncMock(return_value={"authenticated": True})) as mock_req:
        result = _test_coro(client.verify_auth())
        mock_req.assert_awaited_once_with("GET", "/api/v1/auth")
        assert result["authenticated"] is True


def test_list_workspaces():
    """list_workspaces 应请求 /api/v1/workspaces"""
    with patch.object(client, "_request", new=AsyncMock(return_value={"workspaces": [{"slug": "ws1"}]})) as mock_req:
        result = _test_coro(client.list_workspaces())
        mock_req.assert_awaited_once_with("GET", "/api/v1/workspaces")
        assert len(result) == 1
        assert result[0]["slug"] == "ws1"


def test_create_workspace_sends_payload():
    """create_workspace 应 POST 并携带 name"""
    with patch.object(client, "_request", new=AsyncMock(return_value={"slug": "new-ws"})) as mock_req:
        _test_coro(client.create_workspace(name="测试工作区"))
        args, kwargs = mock_req.call_args
        assert args[0] == "POST"
        assert args[1] == "/api/v1/workspaces"
        assert kwargs["json"]["name"] == "测试工作区"


def test_chat_with_workspace():
    """chat_with_workspace 应 POST 到 /chat 并携带 message"""
    with patch.object(client, "_request", new=AsyncMock(return_value={"text": "回答"})) as mock_req:
        _test_coro(client.chat_with_workspace(slug="ws1", message="你好"))
        args, kwargs = mock_req.call_args
        assert args[1] == "/api/v1/workspace/ws1/chat"
        assert kwargs["json"]["message"] == "你好"


def test_search_workspace():
    """search_workspace 应 POST 到 /query"""
    with patch.object(client, "_request", new=AsyncMock(return_value={"matches": []})) as mock_req:
        _test_coro(client.search_workspace(slug="ws1", query="关键词"))
        args, kwargs = mock_req.call_args
        assert args[1] == "/api/v1/workspace/ws1/query"
        assert kwargs["json"]["query"] == "关键词"


def test_missing_api_key_raises():
    """未设置 API key 时应抛 RuntimeError"""
    import importlib
    from anythingllm_mcp import config
    old_key = config.API_KEY
    config.API_KEY = ""
    importlib.reload(client)
    try:
        try:
            client._headers()
            assert False, "应抛出 RuntimeError"
        except RuntimeError as e:
            assert "API_KEY" in str(e) or "API" in str(e)
    finally:
        config.API_KEY = old_key
        importlib.reload(client)
