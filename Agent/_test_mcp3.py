import sys, os
os.chdir(r"D:\LLM\Agent")
sys.path.insert(0, r"D:\LLM\Agent")
# Don't start textual, just run init
import importlib.util
spec = importlib.util.spec_from_file_location("agent_main", r"D:\LLM\Agent\main.py")
# We can't import main.py directly because it starts textual.
# Instead replicate the MCP init logic:
import asyncio, json, threading, time
from pathlib import Path

MCP_TOOL_MAP = {}
_mcp_sessions = {}

def _thread(name, cfg):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _mcp_sessions[name] = {"loop": loop, "ready": threading.Event()}
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []))
    async def run():
        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.list_tools()
                    for t in res.tools:
                        MCP_TOOL_MAP[f"{name}__{t.name}"] = (name, t.name)
                    print(f"[MCP] {name} OK, {len(res.tools)} tools")
                    _mcp_sessions[name]["ready"].set()
                    while True:
                        await asyncio.sleep(3600)
        except Exception as e:
            print(f"[MCP] {name} FAIL: {e}")
            import traceback; traceback.print_exc()
    loop.run_until_complete(run())

cfg = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
for n, c in cfg["mcpServers"].items():
    threading.Thread(target=_thread, args=(n, c), daemon=True).start()

deadline = time.time() + 30
for n in cfg["mcpServers"]:
    ev = _mcp_sessions.get(n, {}).get("ready")
    if ev:
        ev.wait(timeout=max(0, deadline - time.time()))

print("MCP_TOOL_MAP keys:", list(MCP_TOOL_MAP.keys()))
