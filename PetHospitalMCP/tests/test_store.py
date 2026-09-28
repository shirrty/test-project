"""存储层单元测试

运行: pytest tests/test_store.py -v
测试前会在临时路径创建数据库，避免污染正式数据。
"""

import os
import tempfile
from pathlib import Path

# 必须在导入 store 前设置临时 DB 路径
_tmp_dir = tempfile.mkdtemp(prefix="pet_hospital_test_")
os.environ["PET_HOSPITAL_DB"] = str(Path(_tmp_dir) / "test.db")

from pet_hospital_mcp import store  # noqa: E402


def test_create_and_get_pet():
    """登记宠物并按 ID 查询"""
    pet = store.create_pet(
        name="旺财",
        species="狗",
        breed="金毛",
        age=3.5,
        owner_name="张三",
        owner_phone="13800000001",
    )
    assert pet["id"] is not None
    assert pet["name"] == "旺财"

    fetched = store.get_pet(pet["id"])
    assert fetched["name"] == "旺财"
    assert fetched["owner_name"] == "张三"


def test_list_pets_by_owner():
    """按主人姓名过滤宠物"""
    store.create_pet(name="豆豆", species="猫", owner_name="李四", owner_phone="13800000002")
    store.create_pet(name="毛毛", species="猫", owner_name="李四", owner_phone="13800000002")
    store.create_pet(name="大黄", species="狗", owner_name="王五", owner_phone="13800000003")

    pets = store.list_pets(owner_name="李四")
    assert len(pets) == 2
    assert all(p["owner_name"] == "李四" for p in pets)


def test_create_medical_record_and_list():
    """创建病历并列出"""
    pet = store.create_pet(
        name="小黑", species="狗", owner_name="赵六", owner_phone="13800000004"
    )
    record = store.create_medical_record(
        pet_id=pet["id"],
        visit_date="2026-09-24",
        symptoms="咳嗽、流鼻涕",
        diagnosis="上呼吸道感染",
        prescription="阿莫西林 5mg/kg bid 7天",
        doctor="陈医生",
    )
    assert record["id"] is not None
    assert record["pet_id"] == pet["id"]

    records = store.list_medical_records(pet_id=pet["id"])
    assert len(records) == 1
    assert records[0]["diagnosis"] == "上呼吸道感染"


def test_create_record_nonexistent_pet():
    """为不存在的宠物创建病历应抛 ValueError"""
    try:
        store.create_medical_record(
            pet_id=999999, visit_date="2026-09-24", symptoms="测试"
        )
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "999999" in str(e)


def test_update_medical_record():
    """更新病历字段"""
    pet = store.create_pet(name="小白", species="猫", owner_name="钱七", owner_phone="13800000005")
    record = store.create_medical_record(
        pet_id=pet["id"], visit_date="2026-09-24", symptoms="待诊断"
    )
    updated = store.update_medical_record(record["id"], diagnosis="健康", doctor="林医生")
    assert updated["diagnosis"] == "健康"
    assert updated["doctor"] == "林医生"
    assert updated["symptoms"] == "待诊断"  # 未传入的字段保持不变


def test_delete_medical_record():
    """删除病历"""
    pet = store.create_pet(name="小灰", species="狗", owner_name="孙八", owner_phone="13800000006")
    record = store.create_medical_record(
        pet_id=pet["id"], visit_date="2026-09-24", symptoms="误录"
    )
    assert store.delete_medical_record(record["id"]) is True
    assert store.get_medical_record(record["id"]) is None
    assert store.delete_medical_record(record["id"]) is False  # 再删返回 False


def test_list_records_by_date_range():
    """按日期范围过滤病历"""
    pet = store.create_pet(name="小棕", species="狗", owner_name="周九", owner_phone="13800000007")
    store.create_medical_record(pet_id=pet["id"], visit_date="2026-09-01", symptoms="a")
    store.create_medical_record(pet_id=pet["id"], visit_date="2026-09-15", symptoms="b")
    store.create_medical_record(pet_id=pet["id"], visit_date="2026-09-30", symptoms="c")

    records = store.list_medical_records(date_from="2026-09-10", date_to="2026-09-20")
    assert len(records) == 1
    assert records[0]["symptoms"] == "b"
