"""Pydantic 数据模型：工作区、文档、对话结果"""

from pydantic import BaseModel, Field


class Workspace(BaseModel):
    """AnythingLLM 工作区"""

    slug: str = Field(description="工作区唯一标识")
    name: str | None = None
    id: str | None = None
    documents: list[dict] | None = None


class DocumentInfo(BaseModel):
    """文档信息"""

    id: str | None = None
    title: str | None = None
    docpath: str | None = None
    type: str | None = None


class ChatResult(BaseModel):
    """对话结果"""

    text: str = Field(description="回答文本")
    sources: list[dict] = Field(default_factory=list, description="引用来源")
