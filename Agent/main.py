import configparser
import json
import locale
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import trafilatura
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Input, Static

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    with open("config.ini", encoding="utf-8") as f:
        config = configparser.ConfigParser()
        config.read_file(f)
except FileNotFoundError:
    sys.exit("缺少 config.ini：请把 config.example.ini 复制为 config.ini 并填写密钥")

base_url = config["llm"]["base_url"].rstrip("/")
api_key = config["llm"]["api_key"]
model = config["llm"]["model"]

SEARCH_API = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
SEARCH_MODEL = "ernie-4.5-turbo-32k"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_TOOL_OUTPUT = 2000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "searchWeb",
            "description": "联网搜索：根据问题或关键词搜索全网实时信息，返回智能总结的回答摘要与参考来源（标题和 URL）。需要最新资讯、时效性信息时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜索的问题或关键词，例如「今天的新闻头条」"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetchWeb",
            "description": "抓取网页：访问指定 URL，提取页面正文并转为 Markdown（保留标题、段落、列表、表格、链接，去除导航、广告、页脚）。需要阅读某个具体网页的完整内容时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页地址，需以 http:// 或 https:// 开头"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "RunCli",
            "description": "执行本地命令行程序并返回 stdout、stderr 和退出码",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "程序名或可执行文件路径，例如 python、git、node"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "命令参数列表，每个元素一个参数，例如 [\"--version\"]",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取本机当前日期时间（含星期、时区）。问“现在几点/今天几号/现在日期”时优先用这个，不用再调命令行",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "loadSkill",
            "description": "按需加载技能：读取 .agents/skills/ 下某个技能的完整 SKILL.md 全文，或读取该技能目录内指定的附加文件。初始化时只告诉你技能名和简介，需要技能详细用法时再调用本工具",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名：.agents/skills/ 下的子目录名或 SKILL.md frontmatter 里的 name"},
                    "filename": {"type": "string", "description": "可选：要读取的附加文件路径（相对技能目录、项目相对路径或绝对路径）；不传则返回该技能的 SKILL.md 全文"},
                },
                "required": ["name"],
            },
        },
    },
]


# ---------- Skill 发现与动态加载（xAgent 规范） ----------
PROJECT_ROOT = Path.cwd()


def _resolve_skills_dir():
    """技能目录优先读 config.ini 的 [skills] dir；未配置则回退项目根 .agents/skills。"""
    raw = config.get("skills", "dir", fallback="").strip().strip('"').strip("'")
    if raw:
        return Path(os.path.expandvars(raw)).expanduser().resolve()
    return (PROJECT_ROOT / ".agents" / "skills").resolve()


SKILLS_DIR = _resolve_skills_dir()


def parse_frontmatter(text):
    """只取 YAML frontmatter 里的 name / description，不引第三方 yaml 依赖。"""
    meta = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in meta:
                meta[key] = value
    return meta


def discover_skills():
    """扫描 .agents/skills/ 下各 Skill 子目录，仅加载 name/description，不读正文。"""
    found = []
    if not SKILLS_DIR.is_dir():
        return found
    for child in sorted(SKILLS_DIR.iterdir()):
        smd = child / "SKILL.md"
        if not (child.is_dir() and smd.is_file()):
            continue
        try:
            text = smd.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta = parse_frontmatter(text)
        name = meta.get("name") or child.name
        found.append({
            "name": name,
            "description": meta.get("description", ""),
            "dir": child,
            "readme": smd,
        })
    return found


def _resolve_skill_file(skill_dir, filename):
    """把 LLM 给的 filename 解析成技能目录内的真实文件；越界/多义都明确回报。"""
    base = skill_dir.resolve()
    raw = filename.replace("/", "\\")

    exact_in_skill = (skill_dir / raw).resolve()
    if exact_in_skill.is_file():
        return exact_in_skill, None

    project_relative = (PROJECT_ROOT / raw).resolve()
    if project_relative.is_file() and project_relative.is_relative_to(base):
        return project_relative, None

    if Path(filename).is_absolute():
        ap = Path(filename).resolve()
        if ap.is_file() and ap.is_relative_to(base):
            return ap, None

    basename = Path(raw).name
    matches = [p for p in base.rglob(basename) if p.is_file()]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        rel = "\n".join(str(p.relative_to(base)) for p in matches)
        return None, f"找到多个同名文件，请改用相对技能目录的路径后重试：\n{rel}"
    return None, f"在技能目录内找不到文件：{filename}"


def loadSkill(name=None, filename=None, **kwargs):
    if not name:
        raise ValueError("缺少参数 name")
    skills = discover_skills()
    skill = next(
        (s for s in skills if s["name"] == name or s["dir"].name == name),
        None,
    )
    if skill is None:
        available = ", ".join(s["name"] for s in skills) or "（无）"
        return f"未找到名为 {name} 的技能。当前可用：{available}"

    if not filename:
        return clip_output(skill["readme"].read_text(encoding="utf-8", errors="replace"))

    target, err = _resolve_skill_file(skill["dir"], filename)
    if err:
        return f"[loadSkill] {err}"
    if not target.is_relative_to(skill["dir"].resolve()):
        return "[loadSkill] 路径越界：只能读取该技能目录内部的文件"
    return clip_output(target.read_text(encoding="utf-8", errors="replace"))


def decode_console(raw):
    for enc in (locale.getpreferredencoding(False), "mbcs", "gbk", "utf-8"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def clip_output(text):
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + f"\n…（输出过长已截断，完整共 {len(text)} 字符）"
    return text


def searchWeb(query=None, **kwargs):
    if not query:
        raise ValueError("缺少参数 query")
    search_key = config.get("search", "baidu_qianfan_api_key", fallback="").strip()
    if not search_key:
        return ("搜索服务未配置：请在 config.ini 的 [search] 小节填入 "
                "baidu_qianfan_api_key（百度千帆 API Key）后再调用本工具")
    try:
        resp = requests.post(
            SEARCH_API,
            headers={"Authorization": f"Bearer {search_key}"},
            json={
                "messages": [{"role": "user", "content": query}],
                "model": SEARCH_MODEL,
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": 4}],
                "stream": False,
            },
            timeout=60,
        )
    except requests.RequestException as e:
        return f"搜索请求失败：{e}"
    if resp.status_code != 200:
        return f"搜索接口返回错误（HTTP {resp.status_code}）：{clip_output(resp.text)}"
    try:
        data = resp.json()
    except ValueError:
        return "搜索接口返回了无法解析为 JSON 的内容"

    answer = ""
    for choice in data.get("choices") or []:
        content = (choice.get("message") or {}).get("content")
        if content:
            answer = content
            break
    refs = [f"- {ref.get('title') or '(无标题)'}：{ref.get('url') or '(无 URL)'}"
            for ref in data.get("references") or []]
    if not answer and not refs:
        return "搜索接口未返回有效内容"
    parts = []
    if answer:
        parts.append(answer)
    if refs:
        parts.append("参考来源：\n" + "\n".join(refs))
    return "\n\n".join(parts)


def fetchWeb(url=None, **kwargs):
    if not url:
        raise ValueError("缺少参数 url")
    url = str(url)
    if not url.lower().startswith(("http://", "https://")):
        return f"URL 格式错误：必须以 http:// 或 https:// 开头，收到：{url}"
    try:
        proc = subprocess.run(
            ["curl", "-sSL", "--fail", "--ssl-revoke-best-effort",
             "--max-time", "30",
             "-A", USER_AGENT, "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
             url],
            capture_output=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return f"抓取超时：{url}（30 秒内未完成响应）"
    except OSError as e:
        return f"无法启动 curl：{e}"
    if proc.returncode != 0:
        err = decode_console(proc.stderr).strip()
        return f"抓取失败（curl 退出码 {proc.returncode}）：{err or '未知错误'}"

    html = proc.stdout.decode("utf-8", errors="replace")
    try:
        markdown = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        )
    except Exception as e:
        return f"正文提取失败：{e}"
    if not markdown:
        return f"未能从该页面提取到正文（页面可能是纯脚本渲染或无有效内容）：{url}"
    return f"{markdown}\n\n（原始 URL：{url}）"


def RunCli(command=None, args=None, **kwargs):
    if not command:
        raise ValueError("缺少参数 command")
    cmd_list = [str(command)] + [str(a) for a in (args or [])]
    printable = " ".join(cmd_list)
    try:
        proc = subprocess.run(cmd_list, capture_output=True, timeout=60, shell=False)
    except subprocess.TimeoutExpired:
        return f"命令执行超时（60 秒）：{printable}"
    except OSError as e:
        return f"无法执行命令 {command}：{e}"
    out = clip_output(decode_console(proc.stdout).strip())
    err = clip_output(decode_console(proc.stderr).strip())
    parts = []
    if out:
        parts.append(out)
    if proc.returncode != 0:
        parts.append(f"(退出码 {proc.returncode})")
    if err:
        parts.append(err)
    return "\n".join(parts) or f"(无输出，退出码 {proc.returncode})"


def getDate(**kwargs):
    from datetime import datetime
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    now = datetime.now()
    tz = now.astimezone().strftime("%z")
    return now.strftime(f"%Y-%m-%d {weekdays[now.weekday()]} %H:%M:%S {tz}")


TOOL_HANDLERS = {
    "searchWeb": searchWeb,
    "fetchWeb": fetchWeb,
    "RunCli": RunCli,
    "getDate": getDate,
    "loadSkill": loadSkill,
}


# ============ MCP Client 集成 ============
import asyncio
import threading

MCP_TOOL_MAP = {}  # 对外暴露的工具名 -> (server_name, real_tool_name)
_mcp_sessions = {}  # server_name -> {"loop": asyncio.AbstractEventLoop, "ready": threading.Event}


def _open_transport(server_cfg):
    """根据配置选择 stdio 或 streamable-http 传输，返回 async context manager。"""
    from mcp import ClientSession, StdioServerParameters
    url = server_cfg.get("url")
    if url:
        # Streamable HTTP 传输
        from mcp.client.streamable_http import streamablehttp_client
        return streamablehttp_client(url)
    # stdio 传输（默认）
    from mcp.client.stdio import stdio_client
    cmd = server_cfg.get("command")
    args = server_cfg.get("args", [])
    env = server_cfg.get("env")
    params = StdioServerParameters(command=cmd, args=args, env=env)
    return stdio_client(params)


def _mcp_server_thread(server_name, server_cfg):
    """每个 MCP Server 一个后台线程，独立事件循环，常驻连接。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    entry = _mcp_sessions.setdefault(server_name, {"ready": threading.Event()})
    entry["loop"] = loop

    async def _run():
        try:
            transport = _open_transport(server_cfg)
            async with transport as (read, write):
                from mcp import ClientSession
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    registered = 0
                    for t in result.tools:
                        exposed = f"{server_name}__{t.name}"
                        try:
                            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
                            if hasattr(schema, "model_dump"):
                                schema = schema.model_dump()
                            TOOLS.append({
                                "type": "function",
                                "function": {
                                    "name": exposed,
                                    "description": f"[MCP:{server_name}] {t.description or ''}".strip(),
                                    "parameters": schema,
                                },
                            })
                            TOOL_HANDLERS[exposed] = _make_mcp_handler(server_name, t.name, session, loop)
                            MCP_TOOL_MAP[exposed] = (server_name, t.name)
                            registered += 1
                        except Exception as reg_err:
                            print(f"[MCP] 注册 {exposed} 失败：{reg_err}")
                    print(f"[MCP] {server_name} 已连接，注册 {registered} 个工具")
                    entry["ready"].set()
                    while True:
                        await asyncio.sleep(3600)
        except Exception as e:
            print(f"[MCP] {server_name} 连接失败：{e}")
        finally:
            entry["ready"].set()

    loop.run_until_complete(_run())


def _make_mcp_handler(server_name, tool_name, session, loop):
    """返回一个同步的 handler，转发到对应 MCP Server 的 session。"""
    def handler(**kwargs):
        try:
            fut = asyncio.run_coroutine_threadsafe(
                session.call_tool(tool_name, kwargs), loop
            )
            result = fut.result(timeout=60)
            # MCP 返回结构化内容，提取文本
            parts = []
            for c in result.content or []:
                text = getattr(c, "text", None)
                if text:
                    parts.append(text)
            return "\n".join(parts) if parts else str(result)
        except Exception as e:
            return f"[MCP:{server_name}/{tool_name}] 调用失败：{e}"
    return handler


def _find_mcp_config():
    """按顺序找 .mcp.json：cwd → cwd 父目录 → skills 配置所在项目根。"""
    candidates = [Path.cwd() / ".mcp.json"]
    # 向上找 3 层
    for parent in Path.cwd().parents[:3]:
        candidates.append(parent / ".mcp.json")
    # 从 config.ini 的 skills.dir 反推项目根（skills.dir 通常是 <project>/.agents/skills）
    try:
        skills_dir = Path(_resolve_skills_dir())
        for parent in [skills_dir, *skills_dir.parents[:3]]:
            candidates.append(parent / ".mcp.json")
    except Exception:
        pass
    for c in candidates:
        if c.is_file():
            return c
    return None


def init_mcp_servers():
    """找到 .mcp.json，启动所有配置的 MCP Server，并等待连接就绪。"""
    cfg_path = _find_mcp_config()
    if cfg_path is None:
        print("[MCP] 未找到 .mcp.json，跳过 MCP 初始化")
        return
    print(f"[MCP] 读取配置：{cfg_path}")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[MCP] 读取 .mcp.json 失败：{e}")
        return
    for name, srv in (cfg.get("mcpServers") or {}).items():
        # 先在主线程创建好 ready event，避免子线程还没跑就被 wait 跳过
        _mcp_sessions[name] = {"ready": threading.Event()}
        t = threading.Thread(target=_mcp_server_thread, args=(name, srv), daemon=True)
        t.start()
    # 等待所有 server 连接就绪（最多 30 秒）
    deadline = time.time() + 30
    for name in (cfg.get("mcpServers") or {}):
        ev = _mcp_sessions.get(name, {}).get("ready")
        if ev is None:
            continue
        remaining = max(0, deadline - time.time())
        ev.wait(timeout=remaining)


# 启动时初始化 MCP（阻塞等连接就绪，连不上不影响主流程）
init_mcp_servers()


def call_tool(name, arguments):
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"未知的工具：{name}"
    return handler(**arguments)


def parse_arguments(raw):
    text = (raw or "").strip()
    if not text:
        return {}, None
    candidates = [text]
    start = text.find("{")
    if start > 0:
        candidates.append(text[start:])
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            obj, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj, None
    return None, f"参数 JSON 解析失败，原始参数：{text[:200]}"


def stream_once(messages, on_text=None):
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "stream": True,
        },
        stream=True,
        timeout=60,
    )
    if resp.status_code != 200:
        # 把 DeepSeek 返回的错误体打出来，方便定位 400 原因
        err_body = resp.text[:2000]
        raise RuntimeError(f"HTTP {resp.status_code} 调用失败: {err_body}")
    resp.raise_for_status()
    resp.encoding = "utf-8"

    buffer = b""
    done = False
    answer = ""
    tool_calls = {}
    finish_reason = None

    for chunk in resp.iter_content(chunk_size=None):
        if not chunk:
            continue
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                done = True
                break
            payload = json.loads(data)
            choices = payload.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content and not tool_calls:
                if on_text:
                    on_text(content)
                else:
                    print(content, end="", flush=True)
                answer += content
                time.sleep(0.02)
            for tc in delta.get("tool_calls") or []:
                slot = tool_calls.setdefault(
                    tc.get("index", 0),
                    {"id": "", "type": "function",
                     "function": {"name": "", "arguments": ""}},
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
        if done:
            break

    if not on_text:
        print()
    # 防御：如果流式返回里没带 tool_call id，补一个，否则 DeepSeek 下一轮会 400
    import uuid
    for k in tool_calls:
        if not tool_calls[k].get("id"):
            tool_calls[k]["id"] = f"call_{uuid.uuid4().hex[:24]}"
    return answer, [tool_calls[k] for k in sorted(tool_calls)], finish_reason


def build_system_prompt():
    content = ("你是一个乐于助人的助手。运行环境是 Windows（中文系统），"
               "需要执行命令时用 powershell，不要直接用 Linux 的 date/ls/cat 等命令。"
               "可以调用 getDate 获取本机时间、searchWeb 联网搜索实时信息、"
               "fetchWeb 抓取网页正文、RunCli 执行本地命令行程序。"
               "涉及最新资讯或时效性信息时先搜索再回答，"
               "需要阅读具体网页时先抓取正文再总结。")
    skills = discover_skills()
    if skills:
        lines = [
            "",
            "You have access to the following skills:",
        ]
        for s in skills:
            desc = (s["description"] or "").strip()
            lines.append(f"- `{s['name']}`: {desc}" if desc else f"- `{s['name']}`")
        lines.append("Use the `loadSkill` tool to load the full instructions of a skill "
                     "when needed. You can also load additional files referenced by a skill.")
        content += "\n" + "\n".join(lines)
    return content


class ChatApp(App):
    CSS = """
    Static#banner { padding: 1 1 0 1; }
    RichLog#log { height: 1fr; margin: 1 1 0 1; border: round $primary; padding: 0 1; }
    Input { margin: 1 1 1 1; }
    """

    BINDINGS = [("ctrl+c", "quit", "退出")]

    def __init__(self, messages, **kwargs):
        super().__init__(**kwargs)
        self.messages = messages

    def compose(self) -> ComposeResult:
        skill_count = len(discover_skills())
        mcp_count = len(MCP_TOOL_MAP)
        mcp_names = ", ".join(MCP_TOOL_MAP.keys()) or "无"
        yield Static(
            "[b green]LAgent 已启动[/]\n"
            f"[dim]工作目录：{os.getcwd()}[/]\n"
            f"[dim]发现 Skill: {skill_count} 个[/]\n"
            f"[dim]MCP 工具: {mcp_count} 个（{mcp_names}）[/]\n"
            "[dim]输入问题开始对话；Ctrl+C 退出。[/]",
            id="banner",
        )
        yield RichLog(id="log", wrap=True, markup=True, auto_scroll=True)
        yield Input(placeholder="输入问题后 Enter 发送", id="input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if not q:
            return
        inp = self.query_one(Input)
        inp.clear()
        self.query_one(RichLog).write(f"[b cyan]你[/]\n{q}")
        from functools import partial
        self.run_worker(partial(self._handle, q), thread=True)

    def _handle(self, question: str) -> None:
        log = self.query_one(RichLog)

        def w(text: str) -> None:
            self.call_from_thread(log.write, text)

        self.messages.append({"role": "user", "content": question})
        try:
            for _ in range(10):
                chunks = []
                t0 = time.time()
                answer, calls, finish_reason = stream_once(
                    self.messages,
                    on_text=lambda t: chunks.append(t),
                )
                if answer:
                    w(f"[b blue]LAgent[/]\n{answer}")
                self.messages.append(
                    {"role": "assistant", "content": answer or "", "tool_calls": calls}
                    if calls
                    else {"role": "assistant", "content": answer or ""}
                )
                if not calls:
                    break

                for call in calls:
                    name = call["function"]["name"]
                    arguments, err = parse_arguments(call["function"].get("arguments"))
                    if err is None:
                        try:
                            result = call_tool(name, arguments)
                        except Exception as e:
                            result = f"工具执行出错：{e}"
                    else:
                        result = f"{err}，请重新调用该工具并传入正确参数"
                    ms = int((time.time() - t0) * 1000)
                    w(f"[dim]▸ 工具 {name} · {ms} ms[/]")
                    w(clip_output(result))
                    self.messages.append(
                        {"role": "tool", "tool_call_id": call["id"],
                         "content": str(result)}
                    )
        except Exception as e:
            w(f"[red]请求出错：{e}[/]")


def main():
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(),
        }
    ]
    ChatApp(messages).run()


if __name__ == "__main__":
    main()
