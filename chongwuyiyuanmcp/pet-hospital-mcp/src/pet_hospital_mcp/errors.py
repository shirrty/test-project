"""统一结构化错误约定。

所有工具失败都返回同一个错误信封（放在 MCP ``CallToolResult`` 内）::

    {
      "error": {
        "code": "ERROR_CODE",
        "message": "可读错误信息",
        "details": {}
      }
    }
"""

from __future__ import annotations

from typing import Any

#: 工具输入未通过严格校验
VALIDATION_ERROR = "VALIDATION_ERROR"
#: 上游 REST API 超时（含有限重试之后仍超时）
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
#: 无法连接上游 REST API（连接被拒绝、连接中断等）
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
#: 上游 REST API 返回 4xx / 5xx
BACKEND_API_ERROR = "BACKEND_API_ERROR"
#: 上游响应不是合法 JSON，或不符合约定的数据模型
BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
#: MCP 服务自身的兜底错误
INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolFailure(Exception):
    """工具执行失败。

    由 REST 客户端或工具层抛出，携带统一错误信封所需的三个字段；
    不包含 HTTPX / Pydantic / Python 内部堆栈信息。
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_envelope(self) -> dict[str, Any]:
        """返回统一错误结构。"""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }
