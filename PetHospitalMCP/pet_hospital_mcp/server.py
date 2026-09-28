"""MCP Server 入口：宠爱AI病历管理系统

使用 MCP Python SDK 2.x (MCPServer)，stdio 传输，无状态架构。
工具内部捕获异常并返回结构化错误，避免 server 崩溃。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import store

mcp = MCPServer(
    name="pet-hospital-mcp",
    description="宠爱AI病历管理系统：宠物登记与病历管理的 MCP 工具集",
    version="0.1.0",
)


def _ok(data) -> dict:
    """成功响应包装"""
    return {"ok": True, "data": data}


def _err(message: str, detail: str = "") -> dict:
    """错误响应包装"""
    return {"ok": False, "error": message, "detail": detail}


# ============ 宠物工具 ============

@mcp.tool(description="登记一只宠物到病历系统")
def create_pet(
    name: str,
    species: str,
    owner_name: str,
    owner_phone: str,
    breed: str | None = None,
    age: float | None = None,
) -> dict:
    """登记宠物。name=宠物名, species=种类(猫/狗等), owner_name=主人姓名, owner_phone=联系电话, breed=品种(可选), age=年龄(可选)"""
    try:
        pet = store.create_pet(
            name=name,
            species=species,
            owner_name=owner_name,
            owner_phone=owner_phone,
            breed=breed,
            age=age,
        )
        return _ok(pet)
    except Exception as e:
        return _err("登记宠物失败", str(e))


@mcp.tool(description="按 ID 查询宠物信息")
def get_pet(pet_id: int) -> dict:
    """按宠物 ID 查询宠物详细信息"""
    try:
        pet = store.get_pet(pet_id)
        if pet is None:
            return _err("未找到宠物", f"pet_id={pet_id} 不存在")
        return _ok(pet)
    except Exception as e:
        return _err("查询宠物失败", str(e))


@mcp.tool(description="列出所有宠物，可按主人姓名过滤")
def list_pets(owner_name: str | None = None) -> dict:
    """列出宠物列表。owner_name 可选，传入则只返回该主人的宠物"""
    try:
        pets = store.list_pets(owner_name=owner_name)
        return _ok(pets)
    except Exception as e:
        return _err("列出宠物失败", str(e))


# ============ 病历工具 ============

@mcp.tool(description="为宠物创建一条病历记录")
def create_medical_record(
    pet_id: int,
    visit_date: str,
    symptoms: str,
    diagnosis: str | None = None,
    prescription: str | None = None,
    doctor: str | None = None,
) -> dict:
    """创建病历。pet_id=宠物ID, visit_date=就诊日期(YYYY-MM-DD), symptoms=症状,
    diagnosis=诊断(可选), prescription=处方(可选), doctor=主治医生(可选)"""
    try:
        record = store.create_medical_record(
            pet_id=pet_id,
            visit_date=visit_date,
            symptoms=symptoms,
            diagnosis=diagnosis,
            prescription=prescription,
            doctor=doctor,
        )
        return _ok(record)
    except ValueError as e:
        return _err("宠物不存在", str(e))
    except Exception as e:
        return _err("创建病历失败", str(e))


@mcp.tool(description="按 ID 查询病历详情")
def get_medical_record(record_id: int) -> dict:
    """按病历 ID 查询病历详细信息"""
    try:
        record = store.get_medical_record(record_id)
        if record is None:
            return _err("未找到病历", f"record_id={record_id} 不存在")
        return _ok(record)
    except Exception as e:
        return _err("查询病历失败", str(e))


@mcp.tool(description="列出病历，可按宠物ID和日期范围过滤")
def list_medical_records(
    pet_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """列出病历。pet_id 可选按宠物过滤, date_from/date_to 可选按日期范围过滤(YYYY-MM-DD)"""
    try:
        records = store.list_medical_records(
            pet_id=pet_id, date_from=date_from, date_to=date_to
        )
        return _ok(records)
    except Exception as e:
        return _err("列出病历失败", str(e))


@mcp.tool(description="更新病历字段（仅更新传入的字段）")
def update_medical_record(
    record_id: int,
    visit_date: str | None = None,
    symptoms: str | None = None,
    diagnosis: str | None = None,
    prescription: str | None = None,
    doctor: str | None = None,
) -> dict:
    """更新病历。record_id=病历ID, 其余字段可选，仅更新传入的非空字段"""
    try:
        record = store.update_medical_record(
            record_id,
            visit_date=visit_date,
            symptoms=symptoms,
            diagnosis=diagnosis,
            prescription=prescription,
            doctor=doctor,
        )
        if record is None:
            return _err("未找到病历", f"record_id={record_id} 不存在")
        return _ok(record)
    except Exception as e:
        return _err("更新病历失败", str(e))


@mcp.tool(description="删除一条病历记录")
def delete_medical_record(record_id: int) -> dict:
    """按病历 ID 删除病历"""
    try:
        deleted = store.delete_medical_record(record_id)
        if not deleted:
            return _err("未找到病历", f"record_id={record_id} 不存在")
        return _ok({"deleted": True, "record_id": record_id})
    except Exception as e:
        return _err("删除病历失败", str(e))


def main() -> None:
    """启动 MCP server（stdio 传输）"""
    mcp.run()


if __name__ == "__main__":
    main()
