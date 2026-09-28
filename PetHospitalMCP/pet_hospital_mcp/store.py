"""SQLite 存储层：宠物与病历的 CRUD 操作

所有对外函数入口处均调用 init_db()，保证任何调用路径下表都已存在。
"""

from __future__ import annotations

from .db import get_conn, init_db
from .models import MedicalRecord, Pet


# ============ 宠物 ============

def create_pet(
    name: str,
    species: str,
    owner_name: str,
    owner_phone: str,
    breed: str | None = None,
    age: float | None = None,
) -> dict:
    """登记宠物，返回新记录字典"""
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pets (name, species, breed, age, owner_name, owner_phone)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, species, breed, age, owner_name, owner_phone),
        )
        pet_id = cur.lastrowid
        row = conn.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()
    return _pet_row_to_dict(row)


def get_pet(pet_id: int) -> dict | None:
    """按 ID 查宠物，不存在返回 None"""
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()
    return _pet_row_to_dict(row) if row else None


def list_pets(owner_name: str | None = None) -> list[dict]:
    """列出宠物，可按主人姓名过滤"""
    init_db()
    with get_conn() as conn:
        if owner_name:
            rows = conn.execute(
                "SELECT * FROM pets WHERE owner_name = ? ORDER BY id DESC",
                (owner_name,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pets ORDER BY id DESC").fetchall()
    return [_pet_row_to_dict(r) for r in rows]


# ============ 病历 ============

def create_medical_record(
    pet_id: int,
    visit_date: str,
    symptoms: str,
    diagnosis: str | None = None,
    prescription: str | None = None,
    doctor: str | None = None,
) -> dict:
    """创建病历，返回新记录字典。pet_id 不存在时抛 ValueError"""
    init_db()
    with get_conn() as conn:
        # 校验宠物存在
        if not conn.execute("SELECT 1 FROM pets WHERE id = ?", (pet_id,)).fetchone():
            raise ValueError(f"pet_id={pet_id} 不存在")
        cur = conn.execute(
            """INSERT INTO medical_records
               (pet_id, visit_date, symptoms, diagnosis, prescription, doctor)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pet_id, visit_date, symptoms, diagnosis, prescription, doctor),
        )
        rid = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM medical_records WHERE id = ?", (rid,)
        ).fetchone()
    return _record_row_to_dict(row)


def get_medical_record(record_id: int) -> dict | None:
    """按 ID 查病历"""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM medical_records WHERE id = ?", (record_id,)
        ).fetchone()
    return _record_row_to_dict(row) if row else None


def list_medical_records(
    pet_id: int | None = None, date_from: str | None = None, date_to: str | None = None
) -> list[dict]:
    """列出病历，可按宠物 ID 和日期范围过滤"""
    init_db()
    sql = "SELECT * FROM medical_records WHERE 1=1"
    params: list = []
    if pet_id is not None:
        sql += " AND pet_id = ?"
        params.append(pet_id)
    if date_from:
        sql += " AND visit_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND visit_date <= ?"
        params.append(date_to)
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_record_row_to_dict(r) for r in rows]


def update_medical_record(record_id: int, **fields) -> dict | None:
    """更新病历字段，仅更新显式传入的字段。返回更新后的记录或 None"""
    init_db()
    allowed = {"visit_date", "symptoms", "diagnosis", "prescription", "doctor"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_medical_record(record_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [record_id]
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE medical_records SET {set_clause} WHERE id = ?", params
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM medical_records WHERE id = ?", (record_id,)
        ).fetchone()
    return _record_row_to_dict(row) if row else None


def delete_medical_record(record_id: int) -> bool:
    """删除病历，返回是否删除成功"""
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM medical_records WHERE id = ?", (record_id,)
        )
        return cur.rowcount > 0


# ============ 行转字典 ============

def _pet_row_to_dict(row) -> dict:
    return dict(row)


def _record_row_to_dict(row) -> dict:
    return dict(row)
