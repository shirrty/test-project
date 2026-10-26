"""运行期配置。

所有配置来自环境变量，教学场景不提供认证 / CORS / Origin 校验开关。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8000
DEFAULT_BACKEND_TIMEOUT_SECONDS = 5.0
# “有限重试”：首次调用 + 1 次重试，共 2 次尝试。
DEFAULT_BACKEND_MAX_RETRIES = 1
DEFAULT_LOG_LEVEL = "INFO"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是整数，实际值为 {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是数字，实际值为 {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"环境变量 {name} 必须大于 0，实际值为 {raw!r}")
    return value


@dataclass(frozen=True)
class Settings:
    """MCP 服务配置。"""

    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = DEFAULT_MCP_PORT
    backend_base_url: str = DEFAULT_BACKEND_BASE_URL
    backend_timeout_seconds: float = DEFAULT_BACKEND_TIMEOUT_SECONDS
    backend_max_retries: int = DEFAULT_BACKEND_MAX_RETRIES
    log_level: str = DEFAULT_LOG_LEVEL

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构建配置。

        - ``MCP_HOST`` / ``MCP_PORT``：MCP HTTP 监听地址，默认 127.0.0.1:8000
        - ``PET_HOSPITAL_BASE_URL``：Go 宠物医院 REST API 地址
        - ``PET_HOSPITAL_TIMEOUT``：单次上游请求超时（秒）
        - ``PET_HOSPITAL_MAX_RETRIES``：上游调用失败后的重试次数（不含首次）
        - ``MCP_LOG_LEVEL``：日志级别
        """
        host = os.environ.get("MCP_HOST", DEFAULT_MCP_HOST).strip() or DEFAULT_MCP_HOST
        port = _env_int("MCP_PORT", DEFAULT_MCP_PORT)
        if not 1 <= port <= 65535:
            raise RuntimeError(f"环境变量 MCP_PORT 必须在 1-65535 之间，实际值为 {port}")
        base_url = os.environ.get(
            "PET_HOSPITAL_BASE_URL", DEFAULT_BACKEND_BASE_URL
        ).strip() or DEFAULT_BACKEND_BASE_URL
        max_retries = _env_int("PET_HOSPITAL_MAX_RETRIES", DEFAULT_BACKEND_MAX_RETRIES)
        if max_retries < 0:
            raise RuntimeError("环境变量 PET_HOSPITAL_MAX_RETRIES 不能为负数")
        log_level = os.environ.get("MCP_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        return cls(
            mcp_host=host,
            mcp_port=port,
            backend_base_url=base_url.rstrip("/"),
            backend_timeout_seconds=_env_float(
                "PET_HOSPITAL_TIMEOUT", DEFAULT_BACKEND_TIMEOUT_SECONDS
            ),
            backend_max_retries=max_retries,
            log_level=log_level,
        )
