"""AnythingLLM Developer API 异步客户端

基于 httpx 异步实现，对接 AnythingLLM Desktop / Server 的 REST API。
认证：Authorization: Bearer <API_KEY>
"""

from __future__ import annotations

import httpx

from .config import API_BASE_URL, API_KEY, TIMEOUT


def _headers() -> dict[str, str]:
    """构造请求头，含认证"""
    if not API_KEY:
        raise RuntimeError(
            "ANYTHINGLLM_API_KEY 未设置。请在 AnythingLLM UI 的"
            "「设置 → API」生成密钥并写入环境变量。"
        )
    return {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}


async def _request(
    method: str, path: str, **kwargs
) -> dict | list:
    """发送请求并返回 JSON。失败抛异常。"""
    url = f"{API_BASE_URL}{path}"
    kwargs.setdefault("timeout", TIMEOUT)
    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, headers=_headers(), **kwargs)
        resp.raise_for_status()
        # 部分接口返回空体
        if not resp.content:
            return {}
        return resp.json()


# ============ 认证 ============

async def verify_auth() -> dict:
    """验证 API key 是否有效"""
    return await _request("GET", "/api/v1/auth")


# ============ 工作区 ============

async def list_workspaces() -> list[dict]:
    """列出所有工作区"""
    data = await _request("GET", "/api/v1/workspaces")
    return data.get("workspaces", data) if isinstance(data, dict) else data


async def create_workspace(name: str, slug: str | None = None) -> dict:
    """创建工作区"""
    payload = {"name": name}
    if slug:
        payload["slug"] = slug
    return await _request("POST", "/api/v1/workspaces", json=payload)


# ============ 文档 ============

async def upload_document(
    file_path: str, workspaces: str | None = None
) -> dict:
    """上传文档到 AnythingLLM 并 embedding 到指定工作区

    file_path: 本地文件路径
    workspaces: 逗号分隔的工作区 slug，如 "ws1,ws2"，上传后自动 embedding
    """
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    with path.open("rb") as f:
        content = f.read()
    files = {"file": (path.name, content)}
    data = {}
    if workspaces:
        data["workspaces"] = workspaces
    return await _request(
        "POST", "/api/v1/document/upload", files=files, data=data
    )


async def upload_link(url: str, workspaces: str | None = None) -> dict:
    """上传链接（网页）到 AnythingLLM"""
    payload = {"link": url}
    if workspaces:
        payload["workspaces"] = workspaces
    return await _request("POST", "/api/v1/document/upload-link", json=payload)


async def get_workspace_documents(slug: str) -> list[dict]:
    """列出某工作区的所有文档"""
    data = await _request("GET", f"/api/v1/documents/{slug}")
    files = data.get("localFiles", data) if isinstance(data, dict) else data
    return files


# ============ 对话与检索 ============

async def chat_with_workspace(
    slug: str, message: str, mode: str = "chat"
) -> dict:
    """与工作区对话（向量检索 + LLM 生成）

    mode: "chat"（对话）或 "query"（仅检索，不调用 LLM 生成）
    """
    payload = {"message": message, mode: "query" if mode == "query" else "chat"}
    return await _request(
        "POST", f"/api/v1/workspace/{slug}/chat", json=payload
    )


async def search_workspace(slug: str, query: str) -> dict:
    """语义搜索工作区文档（返回匹配片段，不走 LLM）"""
    return await _request(
        "POST",
        f"/api/v1/workspace/{slug}/query",
        json={"query": query},
    )
