"""MCP 服务装配与入口。

使用官方 Python SDK 2.x 的 ``mcp.server.MCPServer``（非 FastMCP），
通过无状态 Streamable HTTP 暴露 2026-07-28 协议端点：

- POST ``/mcp``：无状态 JSON-RPC（不实现旧 ``initialize`` 握手，不使用 ``Mcp-Session-Id``）
- GET  ``/health``：MCP 服务自身健康检查

业务后端仍是唯一的 Go REST API，本服务只通过 HTTP 调用它。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.types.version import LATEST_PROTOCOL_VERSION
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pet_hospital_mcp import __version__
from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.logging_config import configure_logging, get_logger
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools import list_pets as list_pets_module

MCP_ENDPOINT_PATH = "/mcp"
HEALTH_PATH = "/health"

logger = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    upstream_transport: Any = None,
) -> Any:
    """创建 Starlette ASGI 应用（供 ``uvicorn`` 运行或测试使用）。

    Args:
        settings: 运行配置；默认从环境变量读取。
        upstream_transport: 可选的 httpx 异步 Transport（测试中注入 MockTransport）。
    """
    settings = settings or Settings.from_env()

    # 工具闭包通过 provider 取得在 lifespan 中创建的 REST 客户端
    client_provider = SimpleNamespace(client=None)

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        async with PetHospitalClient(settings, transport=upstream_transport) as client:
            client_provider.client = client
            try:
                yield
            finally:
                client_provider.client = None

    mcp = MCPServer(
        name="pet-hospital-mcp",
        version=__version__,
        instructions=(
            "宠物医院 MCP 适配服务，通过无状态 Streamable HTTP（协议 2026-07-28）"
            "把 Go 宠物医院 REST API 暴露为 MCP 工具。"
        ),
        lifespan=lifespan,
        log_level="WARNING",
    )

    list_pets_module.register(mcp, client_provider)

    @mcp.custom_route(HEALTH_PATH, methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "service": "pet-hospital-mcp",
                "version": __version__,
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mcpEndpoint": MCP_ENDPOINT_PATH,
                "backend": {
                    "base_url": settings.backend_base_url,
                },
            }
        )

    return mcp.streamable_http_app(
        streamable_http_path=MCP_ENDPOINT_PATH,
        json_response=True,
        stateless_http=True,
        host=settings.mcp_host,
    )


def main() -> None:
    """同步入口：启动 uvicorn 承载无状态 Streamable HTTP MCP 服务。"""
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    app = create_app(settings)
    logger.info(
        "starting pet-hospital-mcp",
        extra={
            "mcp_host": settings.mcp_host,
            "mcp_port": settings.mcp_port,
            "backend_base_url": settings.backend_base_url,
            "protocol_version": LATEST_PROTOCOL_VERSION,
        },
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
