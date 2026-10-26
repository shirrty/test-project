"""结构化 JSON 日志。

每条工具调用至少记录：
``timestamp`` / ``tool_name`` / ``params`` / ``status`` / ``duration_ms``。

``ownerPhone``、``ownerAddr``、``chipNo`` 及其 snake_case 写法
（``owner_phone``、``owner_addr``、``chip_no``）在任意嵌套层级递归脱敏，
完整敏感数据不会写入日志。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

# 归一化（小写、去掉下划线）后的敏感字段名集合
_SENSITIVE_KEYS = frozenset({"ownerphone", "owneraddr", "chipno"})
_MASK = "******"

# 工具调用事件允许输出的结构化字段
_EVENT_FIELDS = ("tool_name", "params", "status", "duration_ms", "error_code")


def _is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key.lower().replace("_", "") in _SENSITIVE_KEYS


def mask_sensitive(value: Any) -> Any:
    """递归脱敏：命中敏感字段名时整个值替换为掩码。

    支持 dict / list / tuple 等嵌套结构；标量原样返回。
    """
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive(key):
                masked[key] = _MASK
            else:
                masked[key] = mask_sensitive(item)
        return masked
    if isinstance(value, (list, tuple)):
        return [mask_sensitive(item) for item in value]
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event: dict[str, Any] = {}
        for field in _EVENT_FIELDS:
            if hasattr(record, field):
                event[field] = getattr(record, field)
        if event:
            # params 需要递归脱敏；其余字段为安全标量
            if "params" in event:
                event["params"] = mask_sensitive(event["params"])
            payload.update(event)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    """配置 root logger（幂等，重复调用不会叠加 handler）。"""
    global _configured
    if _configured:
        logging.getLogger().setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取模块 logger。"""
    return logging.getLogger(name)


def log_tool_event(
    logger: logging.Logger,
    *,
    tool_name: str,
    params: dict[str, Any] | None,
    status: str,
    started_perf: float,
    error_code: str | None = None,
) -> None:
    """记录一次工具调用事件（成功 / 失败统一入口）。

    调用方传入起始时间戳（``time.perf_counter()``），本函数负责计算耗时。
    """
    duration_ms = round((time.perf_counter() - started_perf) * 1000, 3)
    extra: dict[str, Any] = {
        "tool_name": tool_name,
        "params": params or {},
        "status": status,
        "duration_ms": duration_ms,
    }
    if error_code is not None:
        extra["error_code"] = error_code
    logger.info("tool invocation finished", extra=extra)
