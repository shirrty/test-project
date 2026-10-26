"""REST 客户端测试：参数转发、4xx/5xx、超时/连接异常、非法响应与重试。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from conftest import make_settings, sample_envelope
from pet_hospital_mcp.errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    ToolFailure,
)
from pet_hospital_mcp.rest_client import PETS_PATH, PetHospitalClient

ALL_QUERY_PARAMS = {
    "q": "金毛",
    "name": "豆豆",
    "ownerName": "张伟",
    "ownerPhone": "13800000001",
    "species": "犬",
    "doctor": "王医生",
    "disease": "肠胃炎",
    "status": "已康复",
    "min": 1000.5,
    "max": 2000.0,
    "sortBy": "totalCost",
    "order": "desc",
    "page": 2,
    "pageSize": 50,
}


async def test_list_pets_forwards_path_and_all_params() -> None:
    """场景 1：请求路径与全部 14 个过滤/排序/分页参数正确转发。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.raw_path.decode()
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=sample_envelope())

    async with PetHospitalClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        payload = await client.list_pets(ALL_QUERY_PARAMS)

    assert seen["method"] == "GET"
    assert seen["path"].startswith(PETS_PATH)
    params = seen["params"]
    for key, value in ALL_QUERY_PARAMS.items():
        assert params[key] == str(value), f"{key} 未正确转发"
    assert len(params) == len(ALL_QUERY_PARAMS)
    assert payload["data"]["items"][0]["name"] == "豆豆"


async def test_4xx_returns_backend_api_error() -> None:
    """场景 3：4xx 映射为 BACKEND_API_ERROR，透传可读 message。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": 404, "message": "宠物档案不存在", "data": None, "time": "t"},
        )

    async with PetHospitalClient(make_settings(backend_max_retries=0), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    failure = exc_info.value
    assert failure.code == BACKEND_API_ERROR
    assert "宠物档案不存在" in failure.message
    assert failure.details["http_status"] == 404


async def test_persistent_503_retries_then_fails() -> None:
    """场景 3：可恢复 5xx 有限重试后仍失败，返回 BACKEND_API_ERROR。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"code": 503, "message": "服务不可用", "data": None})

    settings = make_settings(backend_max_retries=1)
    async with PetHospitalClient(settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    assert calls == 2  # 首次 + 1 次重试
    assert exc_info.value.code == BACKEND_API_ERROR
    assert exc_info.value.details["http_status"] == 503


async def test_503_then_200_succeeds_after_retry() -> None:
    """瞬时 503 后恢复：重试拿到成功结果。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, json={"code": 502, "message": "网关错误", "data": None})
        return httpx.Response(200, json=sample_envelope())

    async with PetHospitalClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        payload = await client.list_pets({})

    assert calls == 2
    assert payload["data"]["total"] == 1


async def test_timeout_returns_backend_timeout_after_retries() -> None:
    """场景 4：上游持续超时映射为 BACKEND_TIMEOUT，且只重试有限次数。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("simulated read timeout")

    async with PetHospitalClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    assert calls == 2
    failure = exc_info.value
    assert failure.code == BACKEND_TIMEOUT
    assert failure.details["timeout_seconds"] == 1.0
    assert "超时" in failure.message


async def test_connection_error_returns_backend_unavailable() -> None:
    """场景 4：连接被拒绝/中断映射为 BACKEND_UNAVAILABLE。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    async with PetHospitalClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    assert calls == 2
    assert exc_info.value.code == BACKEND_UNAVAILABLE
    assert exc_info.value.details["base_url"] == "http://backend.test"
    # 不允许把 httpx 内部异常文本直接抛给 MCP 客户端
    assert "connection refused" not in exc_info.value.message


@pytest.mark.parametrize(
    ("body_factory", "reason"),
    [
        (lambda: httpx.Response(200, content=b"{not-json", headers={"content-type": "application/json"}), "non_json"),
        (lambda: httpx.Response(200, json=[1, 2, 3]), "not_object"),
        (lambda: httpx.Response(200, json={"code": 200, "message": "ok", "data": None}), "data_null"),
        (lambda: httpx.Response(200, json={"code": 200, "message": "ok", "data": []}), "data_not_object"),
    ],
)
async def test_invalid_upstream_responses(body_factory: Any, reason: str) -> None:
    """场景 5：非法 JSON 或不符合信封约定，统一映射 BACKEND_INVALID_RESPONSE。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return body_factory()

    async with PetHospitalClient(
        make_settings(backend_max_retries=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    assert exc_info.value.code == BACKEND_INVALID_RESPONSE, reason


async def test_envelope_is_json_serializable() -> None:
    """成功响应必须可被标准 JSON 编码（防止 NaN/Infinity 流出）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"code":200,"message":"ok","data":{"x":NaN}}')

    async with PetHospitalClient(
        make_settings(backend_max_retries=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ToolFailure) as exc_info:
            await client.list_pets({})

    assert exc_info.value.code == BACKEND_INVALID_RESPONSE
    # 兜底检查：错误信封本身可序列化
    json.dumps(exc_info.value.to_envelope(), ensure_ascii=False)
