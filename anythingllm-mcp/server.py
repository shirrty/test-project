import os

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

ANYTHINGLLM_URL = os.environ.get("ANYTHINGLLM_URL", "http://localhost:3001")
ANYTHINGLLM_API_KEY = os.environ.get("ANYTHINGLLM_API_KEY", "").strip()
ANYTHINGLLM_WORKSPACE = os.environ.get("ANYTHINGLLM_WORKSPACE", "ai")

if not ANYTHINGLLM_API_KEY:
    raise RuntimeError(
        "缺少环境变量 ANYTHINGLLM_API_KEY。"
        "请先在 AnythingLLM 设置中生成 API Key，然后设置为环境变量再启动服务。"
    )

mcp = MCPServer("AnythingLLM")


@mcp.tool()
async def ask_workspace(query: str) -> str:
    """Send a question to the local AnythingLLM workspace and return the AI answer grounded in its knowledge."""
    url = f"{ANYTHINGLLM_URL}/api/v1/workspace/{ANYTHINGLLM_WORKSPACE}/chat"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                json={"message": query, "mode": "query"},
                headers={"Authorization": f"Bearer {ANYTHINGLLM_API_KEY}"},
            )
        resp.raise_for_status()
        return resp.json()["textResponse"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ToolError(f"AnythingLLM request failed: {exc}") from exc


app = mcp.streamable_http_app()