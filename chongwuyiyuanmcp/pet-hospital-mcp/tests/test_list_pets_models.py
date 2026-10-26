"""Pydantic 输入/输出模型与日志脱敏测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import sample_envelope, sample_pet
from pet_hospital_mcp.logging_config import mask_sensitive
from pet_hospital_mcp.tools.list_pets import (
    ListPetsInput,
    ListPetsSuccess,
    ToolFailure,
)
from pet_hospital_mcp.errors import VALIDATION_ERROR


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


def test_empty_input_is_valid_and_omits_none() -> None:
    parsed = ListPetsInput.model_validate({})
    assert parsed.to_query_params() == {}


def test_full_valid_input_forwards_every_field() -> None:
    raw = {
        "q": "金毛",
        "name": "豆豆",
        "ownerName": "张伟",
        "ownerPhone": "13800000001",
        "species": "犬",
        "doctor": "王医生",
        "disease": "肠胃炎",
        "status": "已康复",
        "min": 1000,  # JSON 整数应作为费用下界
        "max": 2000.5,
        "sortBy": "totalCost",
        "order": "desc",
        "page": 3,
        "pageSize": 50,
    }
    parsed = ListPetsInput.model_validate(raw)
    params = parsed.to_query_params()
    assert params["min"] == 1000.0
    assert isinstance(params["min"], float)
    assert params["max"] == 2000.5
    assert params["page"] == 3
    assert set(params) == set(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"species": "恐龙"},
        {"status": "出院"},
        {"sortBy": "password"},
        {"order": "sideways"},
        {"page": 0},
        {"page": -3},
        {"pageSize": 0},
        {"pageSize": 501},
        {"min": -0.01},
        {"max": -1},
        {"min": 100, "max": 50},
        {"min": float("nan")},
        {"max": float("inf")},
        {"page": "1"},
        {"page": 1.5},
        {"page": True},
        {"pageSize": "50"},
        {"min": "abc"},
        {"q": 3},
        {"species": 1},
        {"unknown": 1},
        {"min": 100, "hack": True},
    ],
)
def test_invalid_inputs_are_rejected(raw: dict) -> None:
    """场景 2：枚举越界、范围非法、NaN/Infinity、错误类型、未知字段全部拒绝。"""
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate(raw)


def test_json_with_literal_nan_token_rejected() -> None:
    """从 JSON 文本解析出的 NaN / Infinity 同样被 before-validator 拒绝。"""
    import json

    with pytest.raises(ValidationError):
        ListPetsInput.model_validate(json.loads('{"min": NaN}'))
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate(json.loads('{"max": Infinity}'))


def test_validation_error_mapping_is_readable() -> None:
    """工具层把 Pydantic 错误转成可读中文，不泄露内部类型名。"""
    from pet_hospital_mcp.tools.list_pets import _format_validation_errors

    try:
        ListPetsInput.model_validate({"page": 0, "species": "恐龙", "bad": 1})
    except ValidationError as exc:
        message, issues = _format_validation_errors(exc)

    assert "page" in message
    assert "大于或等于 1" in message
    assert "species" in message
    assert "犬" in message and "猫" in message
    assert "bad" in message
    assert all(issue["field"] != "" or issue["reason"] for issue in issues)


# ---------------------------------------------------------------------------
# 成功输出模型
# ---------------------------------------------------------------------------


def test_success_model_parses_go_envelope_data() -> None:
    envelope = sample_envelope()
    result = ListPetsSuccess.model_validate(envelope["data"])
    assert result.total == 1
    assert result.page == 1
    assert result.pageSize == 20
    assert result.totalPages == 1
    assert result.totalCost == 1280.5
    pet = result.items[0]
    assert pet.name == "豆豆"
    assert pet.records == []  # Go 返回 null 时规整为空数组
    assert pet.charges == []


def test_records_and_charges_accept_arrays() -> None:
    pet = sample_pet(
        records=[
            {"id": "R1", "diagnosis": "肠胃炎", "prescription": None},
        ],
        charges=[{"id": "C1", "item": "血常规", "amount": 80.0}],
    )
    result = ListPetsSuccess.model_validate(sample_envelope(items=[pet])["data"])
    record = result.items[0].records[0]
    assert record.prescription == []  # 嵌套 null 同样规整
    assert result.items[0].charges[0].item == "血常规"


def test_success_model_requires_items_list() -> None:
    bad_data = {"items": "not-a-list", "total": 0}
    with pytest.raises(ValidationError):
        ListPetsSuccess.model_validate(bad_data)


def test_success_model_rejects_missing_id() -> None:
    pet = sample_pet()
    del pet["id"]
    with pytest.raises(ValidationError):
        ListPetsSuccess.model_validate(sample_envelope(items=[pet])["data"])


# ---------------------------------------------------------------------------
# 错误信封与日志脱敏
# ---------------------------------------------------------------------------


def test_tool_failure_envelope_shape() -> None:
    failure = ToolFailure(
        VALIDATION_ERROR,
        "参数 page 必须大于或等于 1",
        details={"issues": [{"field": "page", "reason": "greater_than_equal"}]},
    )
    envelope = failure.to_envelope()
    assert set(envelope) == {"error"}
    assert envelope["error"]["code"] == VALIDATION_ERROR
    assert envelope["error"]["message"].startswith("参数")
    assert envelope["error"]["details"]["issues"][0]["field"] == "page"


def test_mask_sensitive_recursive() -> None:
    payload = {
        "params": {
            "ownerPhone": "13800000001",
            "owner_phone": "13800000002",
            "nested": {"ownerAddr": "某地址", "chipNo": "900000000000001"},
            "safe": {"name": "豆豆", "tags": [{"owner_addr": "某地址"}]},
        }
    }
    masked = mask_sensitive(payload)
    params = masked["params"]
    assert params["ownerPhone"] == "******"
    assert params["owner_phone"] == "******"
    assert params["nested"]["ownerAddr"] == "******"
    assert params["nested"]["chipNo"] == "******"
    assert params["safe"]["tags"][0]["owner_addr"] == "******"
    # 非敏感字段原样保留
    assert params["safe"]["name"] == "豆豆"
