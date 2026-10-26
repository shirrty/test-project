"""MCP 工具 ``list_pets``。

严格适配现有 Go REST API ``GET /api/v1/pets``：
- 只暴露 Go API 真实支持的 14 个查询参数，不新增私有业务参数；
- 输入、成功输出、错误输出均使用 Pydantic 模型定义；
- 成功输出对应 Go 成功响应信封中的 ``data``：
  ``items`` / ``total`` / ``page`` / ``pageSize`` / ``totalPages`` / ``totalCost``；
- 兼容 Go 端 ``records`` / ``charges`` 为 ``null`` 或数组两种真实 JSON 表现。
"""

from __future__ import annotations

import json
import math
import time
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from pet_hospital_mcp.errors import (
    BACKEND_INVALID_RESPONSE,
    INTERNAL_ERROR,
    VALIDATION_ERROR,
    ToolFailure,
)
from pet_hospital_mcp.logging_config import get_logger, log_tool_event
from pet_hospital_mcp.rest_client import PetHospitalClient

logger = get_logger(__name__)

TOOL_NAME = "list_pets"

# ---------------------------------------------------------------------------
# 真实后端允许值（来源：GET /api/v1/meta）
# ---------------------------------------------------------------------------

SPECIES_VALUES = ("犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他")
STATUS_VALUES = ("待就诊", "就诊中", "住院中", "已康复", "慢性病随访")
SORT_BY_VALUES = (
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
)
ORDER_VALUES = ("asc", "desc")

# 与 Go GET /api/v1/pets 完全一致的查询参数
ALLOWED_PARAMETERS = (
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
)

Species = Literal[
    "犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"
]
VisitStatus = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SortField = Literal[
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
SortOrder = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# 输入模型（严格校验）
# ---------------------------------------------------------------------------


class ListPetsInput(BaseModel):
    """``list_pets`` 入参，字段名与 Go API 查询参数逐字一致。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    q: Optional[str] = None
    name: Optional[str] = None
    ownerName: Optional[str] = None
    ownerPhone: Optional[str] = None
    species: Optional[Species] = None
    doctor: Optional[str] = None
    disease: Optional[str] = None
    status: Optional[VisitStatus] = None
    min: Annotated[Optional[float], Field(ge=0, allow_inf_nan=False)] = None
    max: Annotated[Optional[float], Field(ge=0, allow_inf_nan=False)] = None
    sortBy: Optional[SortField] = None
    order: Optional[SortOrder] = None
    page: Annotated[Optional[int], Field(ge=1)] = None
    pageSize: Annotated[Optional[int], Field(ge=1, le=500)] = None

    @field_validator("min", "max", mode="before")
    @classmethod
    def _decimal_field(cls, value: Any) -> Any:
        """严格模式下 JSON 整数也应可作为费用上下界；布尔、字符串、NaN/Infinity 拒绝。"""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("必须是非负数字")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("必须是有限数字，不能为 NaN 或 Infinity")
        return number

    @model_validator(mode="after")
    def _check_cost_range(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min 不能大于 max（费用区间下界不得超过上界）")
        return self

    def to_query_params(self) -> dict[str, Any]:
        """导出为可直接转发给 Go API 的查询参数（省略未提供的参数）。"""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# 成功输出模型（对应 Go 信封中的 data）
# ---------------------------------------------------------------------------


class Charge(BaseModel):
    """单条消费明细。"""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    item: Optional[str] = None
    category: Optional[str] = None
    amount: float = 0.0
    doctor: Optional[str] = None
    date: Optional[str] = None


class MedicalRecord(BaseModel):
    """单条历史病历。"""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    visitDate: Optional[str] = None
    doctor: Optional[str] = None
    diagnosis: Optional[str] = None
    symptoms: Optional[str] = None
    treatment: Optional[str] = None
    prescription: list[str] = []
    weightKg: Optional[float] = None
    temperature: Optional[float] = None
    followUp: Optional[str] = None
    charge: Optional[float] = None
    createdAt: Optional[str] = None

    @field_validator("prescription", mode="before")
    @classmethod
    def _null_to_list(cls, value: Any) -> Any:
        return [] if value is None else value


class Pet(BaseModel):
    """宠物档案（列表项）。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    species: Optional[str] = None
    breed: Optional[str] = None
    gender: Optional[str] = None
    ageMonths: Optional[int] = None
    color: Optional[str] = None
    chipNo: Optional[str] = None
    ownerName: Optional[str] = None
    ownerPhone: Optional[str] = None
    ownerAddr: Optional[str] = None
    doctor: Optional[str] = None
    disease: Optional[str] = None
    status: Optional[str] = None
    allergy: Optional[str] = None
    note: Optional[str] = None
    # Go 端在不同写入路径下可能输出 null 或数组，统一规整为数组
    records: list[MedicalRecord] = []
    charges: list[Charge] = []
    totalCost: float = 0.0
    visitCount: int = 0
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

    @field_validator("records", "charges", mode="before")
    @classmethod
    def _null_to_list(cls, value: Any) -> Any:
        return [] if value is None else value


class ListPetsSuccess(BaseModel):
    """``GET /api/v1/pets`` 成功响应 ``data`` 的精确结构。"""

    model_config = ConfigDict(extra="ignore")

    items: list[Pet]
    total: int = 0
    page: int = 0
    pageSize: int = 0
    totalPages: int = 0
    totalCost: float = 0.0


# ---------------------------------------------------------------------------
# 错误输出模型（统一错误信封）
# ---------------------------------------------------------------------------


class ErrorDetails(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ListPetsError(BaseModel):
    """所有失败场景的统一输出。"""

    error: ErrorBody


# ---------------------------------------------------------------------------
# Pydantic 校验错误 -> 可读中文信息（不暴露 Pydantic / Python 内部细节）
# ---------------------------------------------------------------------------

_ALLOWED_HINT = "、".join(ALLOWED_PARAMETERS)


def _allowed_values_hint(field: str) -> str | None:
    if field == "species":
        return "、".join(SPECIES_VALUES)
    if field == "status":
        return "、".join(STATUS_VALUES)
    if field == "sortBy":
        return "、".join(SORT_BY_VALUES)
    if field == "order":
        return "、".join(ORDER_VALUES)
    return None


def _format_validation_errors(exc: ValidationError) -> tuple[str, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    messages: list[str] = []
    for raw in exc.errors():
        loc = ".".join(str(part) for part in raw.get("loc", ()) if part != "")
        kind = raw.get("type", "value_error")
        ctx = raw.get("ctx") or {}
        hint = _allowed_values_hint(loc)
        if kind == "extra_forbidden":
            message = f"不支持的参数 {loc!r}，本工具仅允许以下参数：{_ALLOWED_HINT}"
        elif kind == "literal_error" and hint:
            value = raw.get("input")
            if value is not None:
                message = f"参数 {loc!r} 取值无效：{value!r}，允许的值：{hint}"
            else:
                message = f"参数 {loc!r} 取值无效，允许的值：{hint}"
        elif kind == "string_type":
            message = f"参数 {loc!r} 必须是字符串"
        elif kind == "int_type":
            message = f"参数 {loc!r} 必须是整数"
        elif kind == "float_type":
            message = f"参数 {loc!r} 必须是数字"
        elif kind == "finite_number":
            message = f"参数 {loc!r} 必须是有限数字，不能为 NaN 或 Infinity"
        elif kind == "greater_than_equal":
            message = f"参数 {loc!r} 必须大于或等于 {ctx.get('ge')}"
        elif kind == "less_than_equal":
            message = f"参数 {loc!r} 必须小于或等于 {ctx.get('le')}"
        elif kind == "value_error":
            # 自定义 model_validator / field_validator 的中文消息
            error = ctx.get("error")
            message = str(error) if error is not None else raw.get("msg", "参数取值无效")
        else:
            message = f"参数 {loc!r} 取值无效" if loc else "工具输入无效"
        issue: dict[str, Any] = {"field": loc, "reason": kind}
        if hint and kind == "literal_error":
            issue["allowed"] = hint.split("、")
        issues.append(issue)
        messages.append(message)
    return "；".join(messages) or "工具输入未通过校验", issues


def _failure_result(failure: ToolFailure) -> CallToolResult:
    """把统一错误信封包装为 MCP 工具失败结果（SDK 2.x 仍使用 is_error 标记工具失败）。"""
    envelope = failure.to_envelope()
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))],
        structured_content=envelope,
        is_error=True,
    )


# ---------------------------------------------------------------------------
# 工具注册
# ---------------------------------------------------------------------------

TOOL_DESCRIPTION = """\
查询宠物医院的宠物档案列表（适配 Go REST API：GET /api/v1/pets）。

用途：按关键词、宠物/主人信息、种类、主治医生、疾病、就诊状态、总花费区间进行过滤，
并支持排序与分页；返回当前页档案及其病历/消费明细和汇总信息。

适用场景：用户想查找或浏览宠物档案、按条件筛选宠物、查看消费总额排名、
分页翻阅档案列表时使用。

参数（全部可选，均为 Go 后端真实支持的查询参数）：
- q: 全文关键词
- name: 宠物姓名
- ownerName: 主人姓名
- ownerPhone: 主人电话
- species: 种类，允许值：犬、猫、兔、鸟、仓鼠、爬宠、其他
- doctor: 主治医生姓名
- disease: 疾病/主要诊断
- status: 就诊状态，允许值：待就诊、就诊中、住院中、已康复、慢性病随访
- min / max: 总花费区间（非负数字，min 不得大于 max）
- sortBy: 排序字段，允许值：id、name、ownerName、species、doctor、disease、status、totalCost、visitCount、createdAt、updatedAt
- order: 排序方向，允许值：asc、desc
- page: 页码，从 1 开始
- pageSize: 每页条数，1-500

返回值：Go API 成功响应中的 data 对象，包含 items（档案数组，每条含 records 病历与 charges 消费明细）、
total（总条数）、page（当前页）、pageSize（每页条数）、totalPages（总页数）、totalCost（全部档案总花费）。
失败时返回统一错误结构 {error: {code, message, details}}，isError=true。\
"""


def register(mcp: Any, client_provider: Any) -> None:
    """在给定 ``MCPServer`` 上注册 ``list_pets`` 工具。

    Args:
        mcp: ``mcp.server.MCPServer`` 实例。
        client_provider: 暴露 ``.client`` 属性的对象，值为 ``PetHospitalClient``。
    """

    async def list_pets(
        q: Any = None,
        name: Any = None,
        ownerName: Any = None,
        ownerPhone: Any = None,
        species: Any = None,
        doctor: Any = None,
        disease: Any = None,
        status: Any = None,
        min: Any = None,
        max: Any = None,
        sortBy: Any = None,
        order: Any = None,
        page: Any = None,
        pageSize: Any = None,
        ctx: Context = None,
    ) -> CallToolResult:
        # 签名中的 14 个参数仅用于生成稳定的函数签名；真正校验使用 raw arguments，
        # 以便拒绝未知字段、NaN/Infinity 和类型错误（SDK 生成的参数模型默认忽略额外字段）。
        started = time.perf_counter()
        raw_arguments: Any = {}
        input_params = getattr(ctx, "_input_params", None)
        if input_params is not None:
            raw_arguments = input_params.arguments
        if raw_arguments is None:
            raw_arguments = {}
        if not isinstance(raw_arguments, dict):
            failure = ToolFailure(
                VALIDATION_ERROR,
                "工具参数必须是 JSON 对象",
                details={"issues": [{"field": "", "reason": "arguments_type"}]},
            )
            log_tool_event(
                logger,
                tool_name=TOOL_NAME,
                params=None,
                status="validation_error",
                started_perf=started,
                error_code=VALIDATION_ERROR,
            )
            return _failure_result(failure)

        # 1) 严格输入校验
        try:
            parsed = ListPetsInput.model_validate(raw_arguments)
        except ValidationError as exc:
            message, issues = _format_validation_errors(exc)
            failure = ToolFailure(VALIDATION_ERROR, message, details={"issues": issues})
            log_tool_event(
                logger,
                tool_name=TOOL_NAME,
                params=raw_arguments,
                status="validation_error",
                started_perf=started,
                error_code=VALIDATION_ERROR,
            )
            return _failure_result(failure)

        query_params = parsed.to_query_params()

        # 2) 调用上游并校验成功输出
        try:
            envelope = await client_provider.client.list_pets(query_params)
            try:
                success = ListPetsSuccess.model_validate(envelope.get("data"))
            except ValidationError as exc:
                logger.warning(
                    "backend response data failed model validation",
                    extra={"error_count": len(exc.errors())},
                )
                raise ToolFailure(
                    BACKEND_INVALID_RESPONSE,
                    "宠物医院后端返回的数据结构不符合约定",
                    details={
                        "issues": [
                            {
                                "field": ".".join(str(p) for p in err.get("loc", ())),
                                "reason": err.get("type"),
                            }
                            for err in exc.errors()
                        ]
                    },
                ) from exc
        except ToolFailure as failure:
            log_tool_event(
                logger,
                tool_name=TOOL_NAME,
                params=raw_arguments,
                status="error",
                started_perf=started,
                error_code=failure.code,
            )
            return _failure_result(failure)
        except Exception:  # noqa: BLE001 - 兜底，禁止向客户端泄露内部堆栈
            logger.exception("unexpected error in list_pets")
            failure = ToolFailure(INTERNAL_ERROR, "MCP 服务内部错误，请稍后重试")
            log_tool_event(
                logger,
                tool_name=TOOL_NAME,
                params=raw_arguments,
                status="error",
                started_perf=started,
                error_code=INTERNAL_ERROR,
            )
            return _failure_result(failure)

        data = success.model_dump(mode="json")
        log_tool_event(
            logger,
            tool_name=TOOL_NAME,
            params=raw_arguments,
            status="success",
            started_perf=started,
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
            structured_content=data,
        )

    mcp.add_tool(
        list_pets,
        name=TOOL_NAME,
        title="查询宠物档案列表",
        description=TOOL_DESCRIPTION,
        annotations=ToolAnnotations(
            title="查询宠物档案列表",
            readOnlyHint=True,
            idempotentHint=True,
        ),
    )

    # 用严格输入模型生成对外 JSON Schema（覆盖 SDK 按 Any 签名生成的宽松 schema）
    registered = mcp._tool_manager.get_tool(TOOL_NAME)
    registered.parameters = ListPetsInput.model_json_schema()
