import asyncio

from mcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:7000/mcp") as client:
        result = await client.list_tools()
        print("TOOLS:", [(t.name, getattr(t, "title", None)) for t in result.tools])
        call = await client.call_tool("ask_workspace", {"query": "What do you know?"})
        print("IS_ERROR:", call.is_error)
        for item in call.content:
            print("CONTENT:", getattr(item, "text", item))


asyncio.run(main())