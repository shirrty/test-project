"""Pydantic 数据模型：宠物、病历"""

from datetime import date
from pydantic import BaseModel, Field


class Pet(BaseModel):
    """宠物信息"""

    id: int | None = None
    name: str = Field(description="宠物名字")
    species: str = Field(description="种类，如 猫/狗/兔子")
    breed: str | None = Field(default=None, description="品种")
    age: float | None = Field(default=None, description="年龄（岁）")
    owner_name: str = Field(description="主人姓名")
    owner_phone: str = Field(description="主人联系电话")
    created_at: str | None = None


class MedicalRecord(BaseModel):
    """病历记录"""

    id: int | None = None
    pet_id: int = Field(description="关联的宠物 ID")
    visit_date: str = Field(description="就诊日期，格式 YYYY-MM-DD")
    symptoms: str = Field(description="症状描述")
    diagnosis: str | None = Field(default=None, description="诊断结果")
    prescription: str | None = Field(default=None, description="处方/治疗方案")
    doctor: str | None = Field(default=None, description="主治医生")
    created_at: str | None = None
