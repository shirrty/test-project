"""pytest 公共夹具与辅助函数。

所有测试都不访问真实 Go 服务：
- 上游 REST 调用通过 ``httpx.MockTransport`` 拦截；
- MCP HTTP 端点通过 ``httpx.ASGITransport`` 直接打 Starlette 应用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)
from mcp_types.version import LATEST_PROTOCOL_VERSION as PROTOCOL_VERSION

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.server import MCP_ENDPOINT_PATH
from pet_hospital_mcp.server import create_app

MockHandler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class _LifespanRunner:
    """在同一个专用 asyncio 任务中进入/退出 ASGI lifespan。

    pytest-asyncio 的 async generator fixture 在不同任务中执行 setup/teardown，
    而 SDK 内部的 anyio cancel scope 要求同任务进入与退出；
    这里用一个长驻任务承载整个 lifespan，规避跨任务退出问题。
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._startup_error: BaseException | None = None

    async def _run(self) -> None:
        try:
            async with self._app.router.lifespan_context(self._app):
                self._started.set()
                await self._shutdown.wait()
        except BaseException as exc:  # 把启动异常回传给 start()
            self._startup_error = exc
            self._started.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await self._started.wait()
        if self._startup_error is not None:
            raise self._startup_error

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task is not None:
            await self._task


def make_settings(**overrides: Any) -> Settings:
    """构建指向假上游的测试配置（默认重试 1 次，超时很短）。"""
    defaults: dict[str, Any] = {
        "backend_base_url": "http://backend.test",
        "backend_timeout_seconds": 1.0,
        "backend_max_retries": 1,
        "mcp_host": "127.0.0.1",
        "mcp_port": 8000,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def envelope_params(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """为 2026-07-28 无状态请求附加客户端信封 _meta。"""
    result = dict(params or {})
    result["_meta"] = {
        PROTOCOL_VERSION_META_KEY: PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": "pytest-client", "version": "1.0.0"},
    }
    return result


async def post_jsonrpc(
    client: httpx.AsyncClient,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    include_meta: bool = True,
    request_id: int = 1,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """向无状态 MCP 端点发送单个 JSON-RPC 请求。"""
    params = params or {}
    headers = {
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    # 2026-07-28 Streamable HTTP 要求 Mcp-Name 与 body 中的 name 一致
    if "name" in params:
        headers["Mcp-Name"] = str(params["name"])
    if extra_headers:
        headers.update(extra_headers)
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": envelope_params(params) if include_meta else (params or {}),
    }
    return await client.post(MCP_ENDPOINT_PATH, headers=headers, json=body)


@pytest_asyncio.fixture
async def make_mcp_client(
    request: pytest.FixtureRequest,
) -> Callable[..., Awaitable[httpx.AsyncClient]]:
    """创建挂载了假上游的 MCP 应用与 httpx 客户端。"""
    created: list[tuple[_LifespanRunner, httpx.AsyncClient]] = []

    async def _factory(handler: MockHandler, settings: Settings | None = None) -> httpx.AsyncClient:
        settings = settings or make_settings()
        upstream = httpx.MockTransport(handler)
        app = create_app(settings, upstream_transport=upstream)
        runner = _LifespanRunner(app)
        await runner.start()
        # Host 必须与 SDK DNS 重绑定保护允许的本地地址一致
        http_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        )
        created.append((runner, http_client))
        return http_client

    yield _factory

    for runner, http_client in reversed(created):
        await http_client.aclose()
        await runner.stop()


def sample_pet(pet_id: str = "P001", **overrides: Any) -> dict[str, Any]:
    """返回一份贴近 Go 后端真实结构的宠物档案。"""
    pet: dict[str, Any] = {
        "id": pet_id,
        "name": "豆豆",
        "species": "犬",
        "breed": "金毛",
        "gender": "公",
        "ageMonths": 36,
        "color": "金色",
        "chipNo": "900000000000001",
        "ownerName": "张伟",
        "ownerPhone": "13800000001",
        "ownerAddr": "北京市朝阳区幸福里 1 号",
        "doctor": "王医生",
        "disease": "肠胃炎",
        "status": "已康复",
        "allergy": "青霉素",
        "note": "无",
        "records": None,
        "charges": None,
        "totalCost": 1280.5,
        "visitCount": 3,
        "createdAt": "2026-09-01T10:00:00Z",
        "updatedAt": "2026-09-12T11:30:00Z",
    }
    pet.update(overrides)
    return pet


def sample_envelope(
    *,
    items: list[dict[str, Any]] | None = None,
    total: int = 1,
    page: int = 1,
    page_size: int = 20,
    total_pages: int = 1,
    total_cost: float = 1280.5,
) -> dict[str, Any]:
    """返回 Go API 的完整成功信封。"""
    if items is None:
        items = [sample_pet()]
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
            "totalCost": total_cost,
        },
        "time": "2026-09-20T00:00:00Z",
    }
