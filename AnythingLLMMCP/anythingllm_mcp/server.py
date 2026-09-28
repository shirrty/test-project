"""MCP Server 入口：企业文档AI向量管理系统

使用 MCP Python SDK 2.x (MCPServer)，stdio 传输，无状态架构。
通过 AnythingLLM Developer API 对接已安装的 AnythingLLM Desktop / Server。
工具为 async 函数，内部捕获异常并返回结构化错误。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import client

mcp = MCPServer(
    name="anythingllm-mcp",
    description="企业文档AI向量管理系统：通过 MCP 对接 AnythingLLM，提供文档上传、向量化、语义检索与对话工具",
    version="0.1.0",
)


def _ok(data) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, detail: str = "") -> dict:
    return {"ok": False, "error": message, "detail": detail}


@mcp.tool(description="验证 AnythingLLM API 密钥是否有效")
async def verify_auth() -> dict:
    """验证配置的 API key 是否能通过 AnythingLLM 认证"""
    try:
        result = await client.verify_auth()
        return _ok(result)
    except Exception as e:
        return _err("认证失败", str(e))


@mcp.tool(description="列出 AnythingLLM 中所有工作区")
async def list_workspaces() -> dict:
    """列出所有工作区（workspace）"""
    try:
        workspaces = await client.list_workspaces()
        return _ok(workspaces)
    except Exception as e:
        return _err("列出工作区失败", str(e))


@mcp.tool(description="创建一个新工作区")
async def create_workspace(name: str, slug: str | None = None) -> dict:
    """创建工作区。name=工作区名称, slug=唯一标识(可选，不传则自动生成)"""
    try:
        result = await client.create_workspace(name=name, slug=slug)
        return _ok(result)
    except Exception as e:
        return _err("创建工作区失败", str(e))


@mcp.tool(description="上传本地文档到 AnythingLLM 并向量化到指定工作区")
async def upload_document(
    file_path: str, workspaces: str | None = None
) -> dict:
    """上传文档。file_path=本地文件路径, workspaces=逗号分隔的工作区slug(可选，
    传入则上传后自动 embedding 到这些工作区)"""
    try:
        result = await client.upload_document(file_path=file_path, workspaces=workspaces)
        return _ok(result)
    except FileNotFoundError as e:
        return _err("文件不存在", str(e))
    except Exception as e:
        return _err("上传文档失败", str(e))


@mcp.tool(description="上传网页链接到 AnythingLLM 进行解析与向量化")
async def upload_link(url: str, workspaces: str | None = None) -> dict:
    """上传链接。url=网页URL, workspaces=逗号分隔的工作区slug(可选)"""
    try:
        result = await client.upload_link(url=url, workspaces=workspaces)
        return _ok(result)
    except Exception as e:
        return _err("上传链接失败", str(e))


@mcp.tool(description="列出指定工作区中的所有文档")
async def get_workspace_documents(slug: str) -> dict:
    """列出工作区文档。slug=工作区标识"""
    try:
        docs = await client.get_workspace_documents(slug=slug)
        return _ok(docs)
    except Exception as e:
        return _err("列出文档失败", str(e))


@mcp.tool(description="与工作区对话（向量检索 + LLM 生成回答）")
async def chat_with_workspace(slug: str, message: str) -> dict:
    """与工作区对话。slug=工作区标识, message=用户问题。
    系统会先在工作区文档中做向量检索，再由 LLM 基于检索内容生成回答。"""
    try:
        result = await client.chat_with_workspace(slug=slug, message=message, mode="chat")
        return _ok(result)
    except Exception as e:
        return _err("对话失败", str(e))


@mcp.tool(description="语义搜索工作区文档（返回匹配片段，不走 LLM 生成）")
async def search_workspace(slug: str, query: str) -> dict:
    """语义搜索。slug=工作区标识, query=搜索词。
    仅返回向量检索匹配的文档片段，不调用 LLM 生成回答。"""
    try:
        result = await client.search_workspace(slug=slug, query=query)
        return _ok(result)
    except Exception as e:
        return _err("搜索失败", str(e))


def main() -> None:
    """启动 MCP server（stdio 传输）"""
    mcp.run()


if __name__ == "__main__":
    main()
