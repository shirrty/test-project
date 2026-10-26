"""Go 宠物医院 REST API 客户端。

只通过 HTTP 调用现有 Go 后端，不包含任何业务规则；
超时、有限重试、上游错误到统一错误码的映射都在这里完成。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import anyio
import httpx

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    ToolFailure,
)

PETS_PATH = "/api/v1/pets"
# 仅对可能恢复的网关类 5xx 重试；业务 4xx / 普通 500 不重试
_RETRIABLE_STATUSES = frozenset({502, 503, 504})
_RETRY_BACKOFF_SECONDS = 0.02


class PetHospitalClient:
    """宠物医院 REST API 的异步 HTTP 客户端。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.backend_base_url,
            timeout=httpx.Timeout(settings.backend_timeout_seconds),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PetHospitalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def list_pets(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """调用 ``GET /api/v1/pets``。

        Args:
            params: 已经过工具层校验、可直接转发的查询参数（键名与 Go API 一致）。

        Returns:
            Go API 成功信封解析后的完整 JSON（``code`` / ``message`` / ``data`` / ``time``）。

        Raises:
            ToolFailure: 超时、连接失败、4xx/5xx、非法 JSON 或响应信封不符合约定。
        """
        attempts = self._settings.backend_max_retries + 1
        last_transport_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.get(PETS_PATH, params=dict(params))
            except httpx.TimeoutException as exc:
                last_transport_error = exc
                if attempt < attempts - 1:
                    await anyio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ToolFailure(
                    BACKEND_TIMEOUT,
                    "宠物医院后端服务响应超时，请稍后重试",
                    details={
                        "timeout_seconds": self._settings.backend_timeout_seconds,
                        "path": PETS_PATH,
                    },
                ) from exc
            except httpx.TransportError as exc:
                # 连接被拒绝、连接中断、DNS 失败等
                last_transport_error = exc
                if attempt < attempts - 1:
                    await anyio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ToolFailure(
                    BACKEND_UNAVAILABLE,
                    "无法连接宠物医院后端服务，请确认 Go REST API 已启动",
                    details={
                        "base_url": self._settings.backend_base_url,
                        "path": PETS_PATH,
                    },
                ) from exc

            if response.status_code in _RETRIABLE_STATUSES and attempt < attempts - 1:
                await anyio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            if response.status_code >= 400:
                raise ToolFailure(
                    BACKEND_API_ERROR,
                    self._build_api_error_message(response),
                    details={
                        "http_status": response.status_code,
                        "path": PETS_PATH,
                    },
                )

            return self._parse_envelope(response)

        # 理论上不可达：循环内要么返回、要么抛出
        raise ToolFailure(
            BACKEND_UNAVAILABLE,
            "宠物医院后端服务暂时不可用",
            details={"cause": str(last_transport_error)[:120] if last_transport_error else None},
        )

    @staticmethod
    def _build_api_error_message(response: httpx.Response) -> str:
        """从 Go 错误信封提取可读信息，失败时回退到通用文案。"""
        try:
            body = response.json()
        except ValueError:
            return f"宠物医院后端返回 HTTP {response.status_code} 错误"
        message = body.get("message") if isinstance(body, dict) else None
        if isinstance(message, str) and message.strip():
            return f"宠物医院后端返回错误：{message}"
        return f"宠物医院后端返回 HTTP {response.status_code} 错误"

    def _parse_envelope(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolFailure(
                BACKEND_INVALID_RESPONSE,
                "宠物医院后端返回了无法解析的 JSON",
                details={"path": PETS_PATH, "content_type": response.headers.get("content-type")},
            ) from exc

        if not isinstance(payload, dict):
            raise ToolFailure(
                BACKEND_INVALID_RESPONSE,
                "宠物医院后端响应不是 JSON 对象",
                details={"path": PETS_PATH, "actual_type": type(payload).__name__},
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ToolFailure(
                BACKEND_INVALID_RESPONSE,
                "宠物医院后端响应缺少 data 对象",
                details={"path": PETS_PATH},
            )
        # 防御性确认 JSON 可再次序列化（避免 NaN / Infinity 等非标准值进入 MCP）
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolFailure(
                BACKEND_INVALID_RESPONSE,
                "宠物医院后端响应包含无法编码的 JSON 值",
                details={"path": PETS_PATH},
            ) from exc
        return payload
