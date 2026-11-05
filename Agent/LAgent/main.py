import configparser
import json
import locale
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import trafilatura
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Input, Static, Button
from textual.containers import Horizontal, Vertical, VerticalScroll

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------- 配置：优先读 config.ini；首次运行自动生成模板 ----------
CONFIG_PATH = Path("config.ini")

CONFIG_TEMPLATE = """\
# LAgent 配置（UTF-8 无 BOM）
# 首次启动若没有本文件会自动生成；请先修改 [llm] api_key，再重新启动。

[llm]
base_url = https://api.deepseek.com/v1
api_key = sk-your-api-key-here
model = deepseek-chat
# 上下文窗口：留空则问模型 API 的 /models，拿不到按 128k 并在状态栏标「默认」
context_window = 128k

[search]
# 百度千帆 AI 搜索的 Key（不填 searchWeb 仅返回提示）
baidu_qianfan_api_key =
api = https://qianfan.baidubce.com/v2/ai_search/chat/completions
model = ernie-4.5-turbo-32k

[skills]
# 技能根目录；留空则用 项目根\\.agents\\skills
dir =

[context]
# /compact 归档目录；留空则用 项目根\\.agents\\context
dir =
warn_ratio = 0.70
hard_ratio = 0.95
baseline_ratio = 0.60
summary_target_tokens = 5000

[tui]
show_sidebar = true
max_tool_output = 2000

[sessions]
# /save /new /resume 的会话存档目录
dir = .agents/sessions

# MCP 服务器：每台一节 [mcp.名字]，command 是启动命令，args 是空格分隔参数。
# 未配置时边栏显示「未配置」；示例（去掉注释即可启用）：
# [mcp.example]
# command = npx
# args = -y @modelcontextprotocol/server-everything
"""


def load_config():
    """读取 config.ini。不存在时写一份带 mock 占位的模板，返回 (config, 是否刚生成模板)。"""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        return None, True
    c = configparser.ConfigParser()
    c.read(CONFIG_PATH, encoding="utf-8")
    return c, False


config, _CONFIG_TEMPLATE_WRITTEN = load_config()

if config is not None:
    base_url = config["llm"]["base_url"].rstrip("/")
    api_key = config["llm"]["api_key"]
    model = config["llm"]["model"]
else:
    base_url = api_key = model = ""

SEARCH_API = config.get("search", "api", fallback="https://qianfan.baidubce.com/v2/ai_search/chat/completions") if config else ""
SEARCH_MODEL = config.get("search", "model", fallback="ernie-4.5-turbo-32k") if config else ""
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_TOOL_OUTPUT = config.getint("tui", "max_tool_output", fallback=2000) if config else 2000

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
    {
        "type": "function",
        "function": {
            "name": "recallContext",
            "description": (
                "检索历史上下文归档：当本会话被 /compact 压缩过、当前摘要里缺少你需要的早期细节"
                "（原始文件路径、函数名、命令输出、工具返回结果、错误信息等）时，用本工具在 "
                ".agents/context/ 下的原始上下文归档中做全文关键词检索。"
                "返回命中的原文片段、归档编号和所在位置"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要查找的关键词或文本内容，多个关键词用空格分隔（需全部命中）",
                    },
                    "id": {
                        "type": "string",
                        "description": "可选：只在这个归档编号（形如 20261022-153000）里检索；不传则在所有历史归档中检索",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "traceBatch",
            "description": (
                "查询农产品批次档案：按批次号、产品名或产地关键词，返回该批次的产地与基地、"
                "采收与预冷、分级打包、质检项目与结论、冷链温控记录、承运与下游流向。"
                "供应链追溯类问题先用本工具取数，不要凭印象编造批次号或检测值"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "批次号（如 SC20261012-01）、产品名（如 赣南脐橙）或产地关键词（如 信丰）"},
                    "stage": {
                        "type": "string",
                        "enum": ["all", "basic", "quality", "cold_chain", "logistics", "downstream"],
                        "description": "可选：只看某一段，默认 all 返回全链路",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auditColdChain",
            "description": (
                "冷链合规核查：读取批次档案里的温控记录，按该批次规定的温度区间逐条判定，"
                "返回超限时段、峰值温度、记录空缺与合格结论。冷链是否断链必须用本工具判定"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "批次号（如 GX20261020-05）或产品名"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "makePpt",
            "description": (
                "生成单文件横向翻页网页 PPT（HTML）。你只需要提供每页的结构化内容（标题、要点、大数字等），"
                "模板、样式、翻页交互由本工具自动渲染，**不要**自己去读技能模板 HTML、不要手写 CSS。"
                "适合做汇报、宣传、答辩、发布会式幻灯片。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "整套 PPT 的标题（会显示在浏览器标签和封面）"},
                    "out_dir": {"type": "string", "description": "输出目录（相对项目根或绝对路径），默认 项目/PPT输出"},
                    "slides": {
                        "type": "array",
                        "description": "每页一张，按顺序排列",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layout": {
                                    "type": "string",
                                    "enum": ["cover", "section", "bullets", "big", "closing",
                                             "two-col", "timeline", "stats-row", "quote", "table"],
                                    "description": (
                                        "cover=封面（大标题+副标题，深蓝）；section=章节隔页（黑底）；"
                                        "bullets=要点页（白底，标题+项目符号列表）；big=单一大数字页（深蓝）；"
                                        "stats-row=一行 4 个 KPI 数字（深蓝）；two-col=左右两栏对比（白底）；"
                                        "timeline=横向时间线（白底，给 events: [{year,text}]）；"
                                        "quote=大引用页（黑底，给 quote_text + quote_author）；"
                                        "table=简单表格（白底，给 headers + rows）；"
                                        "closing=结束页（黑底致谢）"
                                    ),
                                },
                                "kicker": {"type": "string", "description": "页面上方小标签（英文/章节号），可选"},
                                "title": {"type": "string", "description": "页面主标题，必填"},
                                "subtitle": {"type": "string", "description": "副标题/一句话引子，cover 和 closing 用"},
                                "bullets": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "bullets 页的要点列表（每条一行）",
                                },
                                "big_num": {"type": "string", "description": "big 页的巨大数字（如 98%、1.2 万）"},
                                "num_label": {"type": "string", "description": "big 页数字下方的一句话解释"},
                                "left_title": {"type": "string", "description": "two-col 左栏标题"},
                                "left_items": {"type": "array", "items": {"type": "string"}, "description": "two-col 左栏要点"},
                                "right_title": {"type": "string", "description": "two-col 右栏标题"},
                                "right_items": {"type": "array", "items": {"type": "string"}, "description": "two-col 右栏要点"},
                                "events": {
                                    "type": "array",
                                    "description": "timeline 页：时间节点列表，每项 {year, text}",
                                    "items": {"type": "object"},
                                },
                                "stats": {
                                    "type": "array",
                                    "description": "stats-row 页：一行 KPI，每项 {value, label}（3-4 个）",
                                    "items": {"type": "object"},
                                },
                                "quote_text": {"type": "string", "description": "quote 页大引用正文"},
                                "quote_author": {"type": "string", "description": "quote 页引用出处"},
                                "headers": {"type": "array", "items": {"type": "string"}, "description": "table 表头"},
                                "rows": {
                                    "type": "array",
                                    "description": "table 行，每行是单元格字符串数组",
                                    "items": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                            "required": ["layout", "title"],
                        },
                    },
                },
                "required": ["title", "slides"],
            },
        },
    },
]


# ---------- Skill 发现与动态加载（xAgent 规范） ----------
def _find_project_root():
    """向上找项目根：最近一层含 .agents 或 .git 的目录，找不到就用当前目录。
    最多往上 4 层，且不越过家目录 / 盘符根，避免把别处的 .agents 当成项目根。
    这样不管在 Agent\\LAgent 还是仓库根目录启动，.agents\\context 都落在同一个项目里。"""
    here = Path.cwd().resolve()
    try:
        home = Path.home().resolve()
    except RuntimeError:
        home = None
    for parent in [here] + list(here.parents)[:4]:
        if home is not None and parent == home:
            break
        if (parent / ".agents").is_dir() or (parent / ".git").is_dir():
            return parent
        if parent == Path(parent.anchor):
            break
    return here


PROJECT_ROOT = _find_project_root()


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
        return skill["readme"].read_text(encoding="utf-8", errors="replace")

    target, err = _resolve_skill_file(skill["dir"], filename)
    if err:
        return f"[loadSkill] {err}"
    if not target.is_relative_to(skill["dir"].resolve()):
        return "[loadSkill] 路径越界：只能读取该技能目录内部的文件"
    return target.read_text(encoding="utf-8", errors="replace")


# 命中这些关键词时自动注入对应技能的完整 SKILL.md
_SKILL_TRIGGERS = {
    "guizang-ppt-skill": ["ppt", "PPT", "幻灯片", "演示", "slide", "keynote",
                          "网页", "html", "HTML", "答辩", "汇报", "宣传"],
}


def _auto_inject_skill(question: str):
    """用户问题命中技能关键词时，返回要注入的 SKILL.md 提示；否则返回 None"""
    if not question:
        return None
    skills = discover_skills()
    for s in skills:
        name = s["name"]
        triggers = _SKILL_TRIGGERS.get(name)
        if not triggers:
            continue
        if not any(t in question for t in triggers):
            continue
        try:
            md = s["readme"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 只取前 ~6KB，避免一次塞太多 token；模型真要细节再自己 loadSkill
        md_brief = md[:6000]
        # 找到模板文件路径，给模型一个明确的起点
        tpl = s["dir"] / "assets" / "template-swiss.html"
        if not tpl.exists():
            tpl = s["dir"] / "assets" / "template.html"
        tpl_hint = f"模板文件（只读它的样式骨架，不要直接复制）：{tpl}\n" if tpl.exists() else ""
        return (
            "[系统提示] 用户要做 PPT/幻灯片。**直接调用 makePpt 工具**，传入结构化内容即可，"
            "不要再去读 guizang 技能的模板 HTML（那个 3500 行的模板会把上下文撑爆）：\n"
            "1. 查资料**优先用 searchWeb 工具**（直接传关键词），不要用 RunCli 跑 "
            "Invoke-WebRequest（PowerShell 抓某些网站会抛异常）；如果 searchWeb 返回"
            "「未配置 API Key」之类的提示，就直接用你已知的常识撰写内容，不要再尝试其它抓取方式；\n"
            "2. 组织成 6-10 页的 slides 数组，每页选一个 layout：\n"
            "   - cover：封面（title=PPT 主标题，subtitle=一句话引子）\n"
            "   - section：章节隔页（title=章节名，kicker=章节号）\n"
            "   - bullets：要点页（title=小节标题，bullets=3-5 条要点）\n"
            "   - big：大数字页（kicker=指标名，big_num=如 98%，num_label=解释）\n"
            "   - closing：结束页（title=谢谢/致谢，subtitle=联系方式）\n"
            "3. 一次调用 makePpt(title=..., slides=[...])，工具会自动生成 HTML 并告诉你文件路径；\n"
            "4. 回复用户时只需要给出文件路径和页数。\n"
        ).format(name=name)
    return None


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
    """联网搜索：优先 Tavily（免费且结构化），其次百度千帆，最后提示未配置"""
    if not query:
        raise ValueError("缺少参数 query")

    provider = (config.get("search", "provider", fallback="tavily") if config else "tavily").strip().lower()
    tavily_key = (config.get("search", "tavily_api_key", fallback="") if config else "").strip()
    baidu_key = (config.get("search", "baidu_qianfan_api_key", fallback="") if config else "").strip()

    # 1) Tavily
    if provider in ("tavily", "") and tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                parts = []
                ans = data.get("answer")
                if ans:
                    parts.append(ans)
                refs = []
                for it in data.get("results") or []:
                    title = it.get("title") or ""
                    url = it.get("url") or ""
                    content = (it.get("content") or "").strip()
                    if content:
                        refs.append(f"### {title}\n{content[:300]}\n来源：{url}")
                    elif title:
                        refs.append(f"- {title}：{url}")
                if refs:
                    parts.append("参考来源：\n" + "\n".join(refs))
                if parts:
                    return clip_output("\n\n".join(parts))
        except (requests.RequestException, ValueError):
            pass

    # 2) 百度千帆（备用）
    if baidu_key:
        try:
            resp = requests.post(
                SEARCH_API,
                headers={"Authorization": f"Bearer {baidu_key}"},
                json={
                    "messages": [{"role": "user", "content": query}],
                    "model": SEARCH_MODEL,
                    "search_source": "baidu_search_v2",
                    "resource_type_filter": [{"type": "web", "top_k": 4}],
                    "stream": False,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("choices"):
                    answer = ""
                    for choice in data.get("choices") or []:
                        content = (choice.get("message") or {}).get("content")
                        if content:
                            answer = content
                            break
                    refs = [f"- {x.get('title') or ''}：{x.get('url') or ''}"
                            for x in data.get("references") or []]
                    out = answer + ("\n\n参考来源：\n" + "\n".join(refs) if refs else "")
                    return clip_output(out)
        except (requests.RequestException, ValueError):
            pass

    return ("搜索服务未配置：请在 config.ini 的 [search] 小节填入 tavily_api_key "
            "（推荐，https://tavily.com 免费注册），或填 baidu_qianfan_api_key 备用")


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


# ---------- 上下文窗口与 Token 估算 ----------
DEFAULT_CONTEXT_WINDOW = 128000      # 拿不到模型真实窗口时按 128k 处理
CONTEXT_WARN_RATIO = config.getfloat("context", "warn_ratio", fallback=0.70) if config else 0.70
CONTEXT_HARD_RATIO = config.getfloat("context", "hard_ratio", fallback=0.95) if config else 0.95
COMPACT_BASELINE_RATIO = config.getfloat("context", "baseline_ratio", fallback=0.60) if config else 0.60
COMPACT_TARGET_TOKENS = config.getint("context", "summary_target_tokens", fallback=5000) if config else 5000


def _parse_window(value):
    """把 "128k" / "128000" / "131,072" 这类写法解析成 token 数，解析失败返回 0"""
    text = str(value or "").strip().lower().replace(",", "").replace("_", "")
    if not text:
        return 0
    scale = 1
    if text.endswith("k"):
        scale, text = 1000, text[:-1]
    elif text.endswith("m"):
        scale, text = 1000000, text[:-1]
    try:
        return int(float(text) * scale)
    except ValueError:
        return 0


def detect_context_window():
    """按 配置 → 模型 API(/models) → 默认 128k 的顺序确定上下文窗口，返回 (tokens, 来源)"""
    configured = _parse_window(config.get("llm", "context_window", fallback=""))
    if configured > 0:
        return configured, "配置"

    # OpenAI 兼容接口通常不带窗口大小；有的服务商在 /models 里给出，能拿到就用
    try:
        resp = requests.get(f"{base_url}/models",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data") if isinstance(data, dict) else data
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                if item.get("id") and item.get("id") != model:
                    continue
                for key in ("context_length", "context_window", "max_context_length",
                            "max_model_len", "max_input_tokens"):
                    value = item.get(key)
                    if isinstance(value, int) and value > 0:
                        return value, "API"
    except (requests.RequestException, ValueError):
        pass
    return DEFAULT_CONTEXT_WINDOW, "默认"


def estimate_text_tokens(text):
    """粗估文本 token 数：中文/全角 1 字≈1 token，英文/代码 4 字符≈1 token（略偏保守）"""
    if not text:
        return 0
    wide = 0
    for ch in text:
        code = ord(ch)
        if (0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF
                or 0xAC00 <= code <= 0xD7AF or 0x3000 <= code <= 0x303F
                or 0xFF00 <= code <= 0xFFEF):
            wide += 1
    narrow = len(text) - wide
    return wide + (narrow + 3) // 4


def messages_token_estimate(messages, include_tools=True):
    """估算整段会话占多少 token（System Prompt、工具定义、对话历史、工具调用与返回）"""
    total = estimate_text_tokens(json.dumps(TOOLS, ensure_ascii=False)) if include_tools else 0
    for msg in messages:
        total += 4          # 每条消息的 role/分隔等结构开销
        total += estimate_text_tokens(msg.get("content") or "")
        if msg.get("tool_calls"):
            total += estimate_text_tokens(json.dumps(msg["tool_calls"], ensure_ascii=False))
    return total


# ---------- 上下文归档：.agents/context/<编号>.jsonl ----------
def _resolve_context_dir():
    """归档目录优先读 config.ini 的 [context] dir；未配置则用 项目根\\.agents\\context"""
    raw = config.get("context", "dir", fallback="").strip().strip('"').strip("'")
    if raw:
        return Path(os.path.expandvars(raw)).expanduser().resolve()
    return (PROJECT_ROOT / ".agents" / "context").resolve()


CONTEXT_DIR = _resolve_context_dir()


# ---------- 会话存档：/save /new /resume ----------
def _resolve_sessions_dir():
    raw = config.get("sessions", "dir", fallback="") if config else ""
    raw = (raw or "").strip().strip('"').strip("'")
    if raw:
        return Path(os.path.expandvars(raw)).expanduser().resolve()
    return (PROJECT_ROOT / ".agents" / "sessions").resolve()


SESSIONS_DIR = _resolve_sessions_dir()


def _session_title(messages):
    """用第一条 user 消息当会话标题，截断 30 字"""
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            return str(m["content"]).strip().splitlines()[0][:30] or "（无标题）"
    return "（无标题）"


def save_session(messages, title=None):
    """把整段会话（含 system prompt 与消息历史）存成 json；返回 (编号, 路径, 错误)"""
    if len(messages) <= 1:
        return None, None, "会话还没有任何对话，不保存"
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        sid = datetime.now().strftime("%Y%m%d-%H%M%S")
        data = {
            "id": sid,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cwd": os.getcwd(),
            "model": model,
            "title": title or _session_title(messages),
            "message_count": len(messages),
            "messages": messages,
        }
        path = SESSIONS_DIR / f"{sid}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return sid, path, None
    except OSError as e:
        return None, None, f"会话保存失败：{e}"


def list_sessions():
    """列出历史会话（最新在前），每项 {id,title,saved_at,count}"""
    if not SESSIONS_DIR.exists():
        return []
    out = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id", p.stem),
                "title": d.get("title", "（无标题）"),
                "saved_at": d.get("saved_at", ""),
                "count": d.get("message_count", 0),
            })
        except (ValueError, OSError):
            continue
    return out


def load_session(sid):
    """按编号读回整段会话消息；返回 (messages, 错误)"""
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        return None, f"找不到会话 {sid}"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d.get("messages") or [], None
    except (ValueError, OSError) as e:
        return None, f"会话读取失败：{e}"


# ---------- AGENTS.md：一次注入、全程复用 ----------
def agents_md_message():
    """当前目录有 AGENTS.md 时，把它作为一条 user 消息注入会话；没有则返回 None"""
    p = Path("AGENTS.md")
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return {"role": "user",
            "content": f"[项目说明 AGENTS.md]\n\n{text}\n\n"
                       "以上为本项目根目录 AGENTS.md 的完整内容，后续请遵循其中约定。"}


def new_archive_id():
    """生成归档编号 YYYYMMDD-HHMMSS；同一秒内重复则追加 -2、-3…"""
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_id = base
    suffix = 1
    while (CONTEXT_DIR / f"{archive_id}.jsonl").exists():
        suffix += 1
        archive_id = f"{base}-{suffix}"
    return archive_id


def save_context_archive(messages, archive_id=None):
    """把当前会话的原始上下文逐条写成 jsonl（保留角色、内容、工具调用参数、工具返回、顺序）。
    返回 (归档编号, 路径, 错误)。写盘 + 回读校验都成功才算成功，否则调用方不能继续压缩。"""
    archive_id = archive_id or new_archive_id()
    path = CONTEXT_DIR / f"{archive_id}.jsonl"
    try:
        CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        return None, None, f"归档写入失败：{e}"

    written = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                json.loads(line)     # 每一行都必须是合法 JSON，才算真的存下来了
                written += 1
    except (OSError, json.JSONDecodeError) as e:
        return None, None, f"归档校验失败：{e}"
    if written != len(messages):
        return None, None, f"归档校验失败：写入 {written} 条，期望 {len(messages)} 条"
    return archive_id, path, None


# ---------- recallContext：本地全文关键词检索 ----------
RECALL_MAX_HITS = 8           # 最多返回几段，避免检索结果又把上下文占满
RECALL_HITS_PER_ARCHIVE = 3   # 单个归档最多取几段，让结果摊到多个归档上
RECALL_SNIPPET_CHARS = 260    # 每段原文字数
RECALL_MAX_OUTPUT = 3000      # 整个返回结果的字数上限


def _all_archives():
    """按编号倒序返回归档文件（编号就是时间戳，最新的排前面）"""
    if not CONTEXT_DIR.is_dir():
        return []
    return sorted(CONTEXT_DIR.glob("*.jsonl"), reverse=True)


def _archive_path(archive_id):
    """把归档编号解析成文件路径；非法编号（带路径分隔符等）返回 None"""
    name = str(archive_id).strip()
    if name.lower().endswith(".jsonl"):
        name = name[:-len(".jsonl")]
    if not name or any(ch in name for ch in ("/", "\\", "..", ":")):
        return None
    return CONTEXT_DIR / f"{name}.jsonl"


def _iter_archive_messages(path):
    """逐行读归档，产出 (行号, 消息字典)；损坏的行跳过，不影响整体检索"""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                yield line_no, msg


def _message_body(msg):
    """把一条消息拼成可检索的文本，工具调用参数与工具返回结果都算在内"""
    parts = []
    if msg.get("content"):
        parts.append(str(msg["content"]))
    if msg.get("tool_calls"):
        parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False))
    return "\n".join(parts)


def _snippet(body, pos):
    """在命中位置附近截一段原文，用省略号标出前后被裁掉的内容"""
    half = RECALL_SNIPPET_CHARS // 2
    start = max(0, pos - half)
    end = min(len(body), start + RECALL_SNIPPET_CHARS)
    piece = " ".join(body[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(body) else "")


def recallContext(text=None, id=None, **kwargs):
    """在 .agents/context/ 的原始归档里检索关键词，返回片段 + 归档编号 + 所在位置"""
    if not text or not str(text).strip():
        raise ValueError("缺少参数 text")
    keywords = [kw.lower() for kw in str(text).split()]

    if id:
        path = _archive_path(id)
        if path is None:
            return f"归档编号不合法：{id}（应形如 20261022-153000）"
        if not path.is_file():
            names = "、".join(p.stem for p in _all_archives()) or "（暂无归档）"
            return f"找不到编号为 {id} 的归档。现有归档：{names}"
        files = [path]
    else:
        files = _all_archives()
    if not files:
        return f"还没有任何上下文归档（{CONTEXT_DIR} 为空或不存在）"

    hits = []
    matched_archives = 0
    for path in files:
        if len(hits) >= RECALL_MAX_HITS:
            break
        per_archive = []
        for line_no, msg in _iter_archive_messages(path):
            body = _message_body(msg)
            low = body.lower()
            found = [kw for kw in keywords if kw in low]
            if not found:
                continue
            pos = min(low.find(kw) for kw in found)
            per_archive.append({
                "id": path.stem,
                "line": line_no,
                "role": msg.get("role") or "?",
                "score": len(found),
                "snippet": _snippet(body, pos),
            })
        if per_archive:
            matched_archives += 1
            per_archive.sort(key=lambda h: h["score"], reverse=True)
            hits.extend(per_archive[:RECALL_HITS_PER_ARCHIVE])

    if not hits:
        names = "、".join(p.stem for p in files) or "（暂无归档）"
        return f"在 {len(files)} 个归档中没有找到「{text}」。已检索：{names}"

    lines = [f"在 {matched_archives} 个归档中找到 {len(hits)} 处匹配（关键词：「{text}」）"]
    for hit in hits:
        lines.append(f"\n[归档 {hit['id']} · 第 {hit['line']} 条 · role={hit['role']}]")
        lines.append(hit["snippet"])
    lines.append(f"\n（单次最多返回 {RECALL_MAX_HITS} 段，已按相关性截断；"
                 f"需要更多内容可指定 id 或换更具体的关键词）")
    out = "\n".join(lines)
    if len(out) > RECALL_MAX_OUTPUT:
        out = out[:RECALL_MAX_OUTPUT] + "\n…（检索结果过长已截断）"
    return out


# ---------- 网页 PPT 渲染器：模型只给结构化内容，Python 套模板 ----------
import html as _html


def _esc(s):
    return _html.escape(str(s) if s is not None else "", quote=False)


def _render_slide(slide, idx):
    layout = slide.get("layout", "bullets")
    kicker = _esc(slide.get("kicker") or f"PAGE {idx+1}")
    title = _esc(slide.get("title") or "")
    subtitle = _esc(slide.get("subtitle") or "")

    if layout == "cover":
        body = f'<div class="kicker">{kicker}</div><h1>{title}</h1>'
        if subtitle:
            body += f'<div class="subtitle">{subtitle}</div>'
        return f'<section class="slide accent">{body}</section>'

    if layout == "section":
        body = f'<div class="kicker">{kicker}</div><h1>{title}</h1>'
        if subtitle:
            body += f'<div class="subtitle">{subtitle}</div>'
        return f'<section class="slide dark">{body}</section>'

    if layout == "big":
        num = _esc(slide.get("big_num") or "0")
        label = _esc(slide.get("num_label") or "")
        body = f'<div class="kicker">{kicker}</div><div class="big-num">{num}</div>'
        if label:
            body += f'<div class="num-label">{label}</div>'
        return f'<section class="slide accent">{body}</section>'

    if layout == "closing":
        body = f'<h1>{title}</h1>'
        if subtitle:
            body += f'<div class="subtitle">{subtitle}</div>'
        return f'<section class="slide dark">{body}</section>'

    if layout == "quote":
        qt = _esc(slide.get("quote_text") or title)
        qa = _esc(slide.get("quote_author") or "")
        body = f'<div class="kicker">{kicker}</div><div class="quote-text">{qt}</div>'
        if qa:
            body += f'<div class="quote-author">—— {qa}</div>'
        return f'<section class="slide dark">{body}</section>'

    if layout == "two-col":
        lt = _esc(slide.get("left_title") or "左栏")
        rt = _esc(slide.get("right_title") or "右栏")
        li = "".join(f"<li>{_esc(x)}</li>" for x in (slide.get("left_items") or [])) or "<li>（无）</li>"
        ri = "".join(f"<li>{_esc(x)}</li>" for x in (slide.get("right_items") or [])) or "<li>（无）</li>"
        body = (f'<div class="kicker">{kicker}</div><h2>{title}</h2>'
                f'<div class="two-col"><div class="col"><h3>{lt}</h3><ul>{li}</ul></div>'
                f'<div class="col"><h3>{rt}</h3><ul>{ri}</ul></div></div>')
        return f'<section class="slide light">{body}</section>'

    if layout == "timeline":
        events = slide.get("events") or []
        items = ""
        for ev in events:
            y = _esc(ev.get("year") or ev.get("date") or "")
            t = _esc(ev.get("text") or "")
            items += f'<div class="tl-item"><div class="tl-year">{y}</div><div class="tl-text">{t}</div></div>'
        body = f'<div class="kicker">{kicker}</div><h2>{title}</h2><div class="timeline">{items}</div>'
        return f'<section class="slide light">{body}</section>'

    if layout == "stats-row":
        stats = slide.get("stats") or []
        cells = ""
        for s in stats:
            v = _esc(s.get("value") or s.get("big_num") or "")
            l = _esc(s.get("label") or s.get("num_label") or "")
            cells += f'<div class="stat"><div class="v">{v}</div><div class="l">{l}</div></div>'
        body = f'<div class="kicker">{kicker}</div><h2>{title}</h2><div class="stats-row">{cells}</div>'
        return f'<section class="slide accent">{body}</section>'

    if layout == "table":
        headers = slide.get("headers") or []
        rows = slide.get("rows") or []
        th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
        trs = ""
        for row in rows:
            tds = "".join(f"<td>{_esc(c)}</td>" for c in row)
            trs += f"<tr>{tds}</tr>"
        body = (f'<div class="kicker">{kicker}</div><h2>{title}</h2>'
                f'<table class="simple"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>')
        return f'<section class="slide light">{body}</section>'

    # 默认 bullets
    items = slide.get("bullets") or []
    items_html = "".join(f"<li>{_esc(x)}</li>" for x in items) or "<li>（无要点）</li>"
    body = f'<div class="kicker">{kicker}</div><h2>{title}</h2><ul class="bullets">{items_html}</ul>'
    return f'<section class="slide light">{body}</section>'


def makePpt(title=None, slides=None, out_dir=None, **kwargs):
    """按结构化 slides 渲染单文件 HTML PPT"""
    if not title or not slides:
        raise ValueError("makePpt 需要 title 和 slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("slides 必须是非空数组")

    tpl_path = PROJECT_ROOT / ".agents" / "ppt_engine" / "template.html"
    if not tpl_path.exists():
        return f"找不到渲染模板：{tpl_path}"
    tpl = tpl_path.read_text(encoding="utf-8")

    slide_html = "\n".join(_render_slide(s, i) for i, s in enumerate(slides))
    deck = (tpl.replace("__DECK_TITLE__", _esc(title))
               .replace("__TOTAL__", str(len(slides)))
               .replace("__SLIDES__", slide_html))

    out_root = PROJECT_ROOT / "项目" / "PPT输出" if not out_dir else (
        Path(out_dir) if Path(out_dir).is_absolute() else PROJECT_ROOT / out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c for c in title if c not in '\\/:*?"<>|')[:40] or "deck"
    out_file = out_root / f"{safe_title}.html"
    out_file.write_text(deck, encoding="utf-8")
    return (f"已生成网页 PPT：{out_file}\n"
            f"共 {len(slides)} 页，用浏览器打开后按 → / 空格 翻页。")


# ---------- /compact：上下文压缩 ----------
COMPACT_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def render_history_for_summary(messages, token_budget, archive_id):
    """把历史消息渲染成压缩请求用的纯文本；超出预算时优先保留最近的消息"""
    chunks = []
    for i, msg in enumerate(messages, 1):
        body = _message_body(msg)
        role = msg.get("role") or "?"
        tool_id = msg.get("tool_call_id")
        head = f"### [{i}] {role}" + (f"（tool_call_id={tool_id}）" if tool_id else "")
        chunks.append(f"{head}\n{body}\n")

    kept, used, dropped = [], 0, 0
    for chunk in reversed(chunks):        # 从最新往旧排，超出单次请求预算的旧消息先舍弃
        cost = estimate_text_tokens(chunk)
        if kept and used + cost > token_budget:
            dropped += 1
            continue
        kept.append(chunk)
        used += cost
    body_text = "".join(reversed(kept))
    if dropped:
        body_text = (f"（提示：较早的 {dropped} 条消息因超出单次请求长度未纳入本次摘要，"
                     f"它们已完整保存在归档 {archive_id} 中，可让 Agent 用 recallContext 检索。）\n\n"
                     + body_text)
    return body_text


def request_summary(history_text):
    """调用 LLM 生成摘要。刻意不传 tools：压缩期间不允许发生任何工具调用"""
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": COMPACT_PROMPT},
                    {"role": "user", "content": history_text},
                ],
                "stream": False,
            },
            timeout=180,
        )
    except requests.RequestException as e:
        return None, None, f"压缩请求失败：{e}"
    if resp.status_code != 200:
        return None, None, f"压缩接口返回错误（HTTP {resp.status_code}）：{clip_output(resp.text)}"

    try:
        data = resp.json()
    except ValueError:
        return None, None, "压缩接口返回了无法解析为 JSON 的内容"
    choices = data.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    content = (message.get("content") or "").strip()
    if not content:
        # 模型仍然试图发起工具调用时，也不接受：压缩阶段只允许产出摘要文本
        if message.get("tool_calls"):
            return None, None, "压缩模型试图调用工具，已拒绝；压缩阶段只允许生成摘要文本"
        return None, None, "压缩接口未返回摘要内容"
    return content, data.get("usage"), None


def run_compaction(messages, window_tokens, notify=lambda text: None):
    """执行一次完整压缩：归档 → 让 LLM 生成摘要 → 用摘要替换历史。
    返回 (是否成功, 新消息列表, 归档编号, 错误信息)；失败时新消息列表为 None。"""
    if len(messages) <= 1:
        return False, None, None, "当前没有可压缩的历史消息（只有 System Prompt）"

    # 1) 先归档原始上下文，必须成功（包含系统提示、工具调用参数、工具返回结果、原始顺序）
    archive_id, path, err = save_context_archive(messages)
    if err:
        return False, None, None, err
    notify(f"[dim]▸ 原始上下文已归档：{path.name}（{len(messages)} 条消息）[/]")

    # 2) 调用 LLM 生成摘要（不含 system prompt，它本来就会被原样保留）
    budget = max(int(window_tokens * 0.75), 4000)
    history_text = render_history_for_summary(messages[1:], budget, archive_id)
    notify(f"[dim]▸ 正在生成摘要（送入约 {estimate_text_tokens(history_text)} tokens）…[/]")
    summary, usage, err = request_summary(history_text)
    if err:
        return False, None, archive_id, f"{err}（原始上下文未改动，可重新执行 /compact）"

    # 3) 用摘要替换历史，System Prompt 与工具定义原样保留
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = (f"此前上下文已于 {stamp} 完成压缩，原始上下文保存在编号为 {archive_id} 的归档文件中。"
            f"如需查找历史信息，请使用 recallContext 工具。")
    content = (f"【历史上下文摘要｜压缩于 {stamp}｜摘要约 {estimate_text_tokens(summary)} tokens】\n\n"
               f"{summary}\n\n---\n\n> {note}")
    new_messages = [messages[0], {"role": "user", "content": content}]
    return True, new_messages, archive_id, None


# ---------- 农业供应链溯源：示例批次档案与业务工具 ----------
AGRI_DATA_DIR = (PROJECT_ROOT / ".agents" / "agri-data").resolve()
AGRI_MAX_OUTPUT = 2600           # 单次工具输出上限，避免一次查询又撑满上下文
TRACE_MAX_BATCHES = 3            # 一次最多展开几个批次
COLD_RECORD_MAX_GAP_HOURS = 6    # 相邻温控记录超过这个间隔，视为记录缺失（正常按 4~6 小时巡检）


def _load_batches():
    """读取本地批次档案（教学演示用的示例数据）；文件缺失或格式错误时返回空列表"""
    try:
        raw = (AGRI_DATA_DIR / "batches.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("batches") or []
    return [b for b in data if isinstance(b, dict)]


def _clip_output(text):
    if len(text) <= AGRI_MAX_OUTPUT:
        return text
    return text[:AGRI_MAX_OUTPUT] + (f"\n…（输出已截断，完整内容 {len(text)} 字；"
                                     f"可加 stage 参数只看某一段）")


def _match_batches(keyword):
    """批次号 / 产品名 / 产地 / 主体的包含式匹配（不区分大小写）"""
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    hits = []
    for b in _load_batches():
        origin = b.get("origin") or {}
        hay = " ".join([
            str(b.get("batch_id", "")),
            str(b.get("product", "")),
            str(b.get("trace_code", "")),
            str(b.get("owner", "")),
            str(origin.get("province", "")),
            str(origin.get("county", "")),
            str(origin.get("base", "")),
        ]).lower()
        if kw in hay:
            hits.append(b)
    return hits


def _fmt_basic(b):
    origin = b.get("origin") or {}
    harvest = b.get("harvest") or {}
    packing = b.get("packing") or {}
    return "\n".join([
        f"【批次 {b.get('batch_id', '-')}】{b.get('product', '-')}｜{b.get('category', '-')}"
        f"｜{b.get('grade', '-')}",
        f"- 责任主体：{b.get('owner', '-')}",
        f"- 产地：{origin.get('province', '')}{origin.get('county', '')} "
        f"{origin.get('base', '')}（供应方 {origin.get('supplier', '-')}）",
        f"- 采收：{harvest.get('date', '-')}，{harvest.get('method', '-')}，"
        f"{harvest.get('precool', '未记录预冷')}",
        f"- 分级打包：{packing.get('plant', '-')} {packing.get('date', '-')}，"
        f"{packing.get('package', '-')}，{packing.get('qty', '-')}",
        f"- 追溯码：{b.get('trace_code', '-')}",
    ])


def _fmt_quality(b):
    q = b.get("quality") or {}
    lines = [f"- 质检：{q.get('inspection_date', '-')} 由 {q.get('lab', '-')} 出具，"
             f"结论「{q.get('conclusion', '-')}」"]
    for it in (q.get("items") or []):
        flag = "达标" if it.get("ok") else "超标"
        lines.append(f"    · {it.get('name', '-')} {it.get('value', '-')}{it.get('unit', '')}"
                     f"（限量 {it.get('limit', '-')}，{flag}）")
    return "\n".join(lines)


def _fmt_cold_chain(b):
    cc = b.get("cold_chain") or {}
    recs = cc.get("records") or []
    lines = [f"- 冷链：{cc.get('mode', '-')} {cc.get('target_low', '-')}~"
             f"{cc.get('target_high', '-')}℃，{cc.get('device', '-')}，共 {len(recs)} 条记录"]
    for r in recs[:12]:
        lines.append(f"    · {r.get('time', '-')}  {r.get('temp', '-')}℃  {r.get('place', '-')}")
    if len(recs) > 12:
        lines.append(f"    · 其余 {len(recs) - 12} 条略（要判定是否超限请用 auditColdChain）")
    return "\n".join(lines)


def _fmt_logistics(b):
    lg = b.get("logistics") or {}
    route = " → ".join(lg.get("route") or []) or "-"
    return (f"- 承运：{lg.get('carrier', '-')}，{lg.get('ship_date', '-')} 发运，"
            f"{lg.get('arrive', '-')} 到货\n- 路线：{route}")


def _fmt_downstream(b):
    rows = [f"    · {d.get('buyer', '-')}：{d.get('qty', '-')}，{d.get('ship', '-')} 发出"
            for d in (b.get("downstream") or [])]
    return "- 下游流向：\n" + ("\n".join(rows) if rows else "    · 无记录")


def traceBatch(keyword=None, stage="all", **kwargs):
    """按批次号 / 产品名 / 产地查批次档案，stage 控制只返回某一段"""
    batches = _match_batches(keyword)
    if not batches:
        known = "、".join(f"{b.get('batch_id', '?')}（{b.get('product', '?')}）"
                          for b in _load_batches()) or "（无）"
        return f"没有匹配「{keyword}」的批次档案。现有批次：{known}"
    stage = (stage or "all").strip()
    parts_order = ["quality", "cold_chain", "logistics", "downstream"]
    formatters = {
        "quality": _fmt_quality,
        "cold_chain": _fmt_cold_chain,
        "logistics": _fmt_logistics,
        "downstream": _fmt_downstream,
    }
    blocks = []
    for b in batches[:TRACE_MAX_BATCHES]:
        parts = [_fmt_basic(b)]
        for key in parts_order:
            if stage in ("all", key):
                parts.append(formatters[key](b))
        if b.get("notes"):
            parts.append(f"- 备注：{b['notes']}")
        blocks.append("\n".join(parts))
    shown = min(len(batches), TRACE_MAX_BATCHES)
    head = f"命中 {len(batches)} 个批次，展开前 {shown} 个：\n"
    return _clip_output(head + "\n\n".join(blocks))


def _parse_record_time(text):
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def _record_temp(record):
    try:
        return float(record.get("temp"))
    except (TypeError, ValueError):
        return None


def auditColdChain(keyword=None, **kwargs):
    """按批次规定的温度区间逐条判定温控记录，给出超限时段、峰值、记录空缺与结论"""
    batches = _match_batches(keyword)
    if not batches:
        return (f"没有匹配「{keyword}」的批次档案，无法核查冷链。"
                f"可先用 traceBatch 查询可用批次。")
    reports = []
    for b in batches[:TRACE_MAX_BATCHES]:
        cc = b.get("cold_chain") or {}
        low, high = cc.get("target_low"), cc.get("target_high")
        recs = [r for r in (cc.get("records") or []) if _parse_record_time(r.get("time"))]
        if not recs or low is None or high is None:
            reports.append(f"【{b.get('batch_id', '-')}】温控记录或温度区间缺失，需人工核实。")
            continue
        over = [r for r in recs if _record_temp(r) is not None
                and (_record_temp(r) > high or _record_temp(r) < low)]
        gaps = []
        for prev, cur in zip(recs, recs[1:]):
            hours = (_parse_record_time(cur["time"]) - _parse_record_time(prev["time"])).total_seconds() / 3600
            if hours > COLD_RECORD_MAX_GAP_HOURS:
                gaps.append(f"{prev['time']} → {cur['time']}（间隔 {hours:.1f} 小时）")
        segments = []
        for r in over:
            if segments:
                last = _parse_record_time(segments[-1][-1]["time"])
                now = _parse_record_time(r["time"])
                if (now - last).total_seconds() / 3600 <= COLD_RECORD_MAX_GAP_HOURS:
                    segments[-1].append(r)
                    continue
            segments.append([r])

        def _dev(r):
            t = _record_temp(r)
            return t - high if t > high else low - t

        lines = [f"【{b.get('batch_id', '-')}】{b.get('product', '-')}｜规定区间 "
                 f"{low}~{high}℃｜有效记录 {len(recs)} 条"]
        if over:
            peak = max(over, key=_dev)
            lines.append(f"- 温控判定：不合格，{len(over)}/{len(recs)} 条记录超出区间")
            for seg in segments:
                lines.append(f"- 超限时段：{seg[0]['time']} ~ {seg[-1]['time']}，"
                             f"地点 {seg[0].get('place', '-')}，共 {len(seg)} 条")
                for r in seg:
                    lines.append(f"    · {r['time']}  {r.get('temp')}℃")
            lines.append(f"- 峰值：{peak['time']} {peak.get('temp')}℃（超出区间 {_dev(peak):.1f}℃）")
        else:
            lines.append("- 温控判定：所有实测温度都在区间内")
        if gaps:
            lines.append(f"- 记录空缺（超过 {COLD_RECORD_MAX_GAP_HOURS} 小时无记录，"
                         f"无法证明全程受控）：")
            for g in gaps:
                lines.append(f"    · {g}")
        if over:
            verdict = "不合格：存在超温时段，建议复核装卸与暂存环节并评估该批货品质"
        elif gaps:
            verdict = "待补充：温度未超限但记录不连续，需补齐温控记录后方可放行"
        else:
            verdict = "合格：全程受控，可正常放行"
        lines.append(f"- 结论：{verdict}")
        reports.append("\n".join(lines))
    return _clip_output("\n\n".join(reports))


TOOL_HANDLERS = {
    "searchWeb": searchWeb,
    "fetchWeb": fetchWeb,
    "RunCli": RunCli,
    "getDate": getDate,
    "loadSkill": loadSkill,
    "recallContext": recallContext,
    "traceBatch": traceBatch,
    "auditColdChain": auditColdChain,
    "makePpt": makePpt,
}


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


def stream_once(messages, on_text=None, with_usage=True):
    body = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
    }
    if with_usage:
        # 让 API 在流末尾附带 usage，用于上下文占用统计；拿不到就退化为本地估算
        body["stream_options"] = {"include_usage": True}

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        stream=True,
        timeout=60,
    )
    if resp.status_code == 400 and with_usage:
        # 有些服务端不认 stream_options，退回不带它的请求，保证对话本身不受影响
        resp.close()
        return stream_once(messages, on_text=on_text, with_usage=False)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    buffer = b""
    done = False
    answer = ""
    tool_calls = {}
    finish_reason = None
    usage = None

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
            if payload.get("usage"):
                usage = payload["usage"]
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
    return answer, [tool_calls[k] for k in sorted(tool_calls)], finish_reason, usage


def build_system_prompt():
    content = (
        "你是「农链溯源」AI Agent，服务农产品供应链与溯源场景：产地仓与合作社、冷链承运方、"
        "销地采购与质检人员都要靠你查清一批货的来路和去向。"
        "运行环境是 Windows（中文系统），需要执行命令时用 powershell，"
        "不要直接用 Linux 的 date/ls/cat 等命令。\n"
        "职责与工具用法：\n"
        "1. 批次追溯：先调 traceBatch 按批次号、产品名或产地取档案，再讲清产地与基地、"
        "采收与预冷、分级打包、质检项目、冷链、承运路线与下游流向。"
        "不要凭印象编造批次号、检测值、客户名。\n"
        "2. 冷链合规：是否断链、是否超温一律用 auditColdChain 判定，不要自己目测温度记录下结论；"
        "出现记录空缺时，要明确指出「无法证明全程受控」。\n"
        "3. 时效与规范：农残限量、检疫要求、追溯管理办法等硬指标用 searchWeb 查最新标准或政策，"
        "必要时用 fetchWeb 读正文，回答里说明依据来源；查不到就说查不到。\n"
        "4. 其他工具：getDate 取本机时间，RunCli 执行本地命令行程序，loadSkill 按需加载技能说明。\n"
        "回答要求：先给结论（合格 / 不合格 / 需复核），再给依据和数据；"
        "涉及流向时列明下游客户与数量；发现质检不合格时，提示冻结该批次流向并追溯用药记录。\n"
        "批次档案在 .agents/agri-data/batches.json，是教学演示用的示例数据，"
        "涉及合规判定时要说明依据是示例限值，不能当作权威标准。\n"
        "如果历史上下文被压缩过、摘要里缺少你需要的老细节（比如之前查过的某个批次温控数据），"
        "用 recallContext 工具检索 .agents/context/ 里的原始归档。"
    )
    skills = discover_skills()
    if skills:
        lines = [
            "",
            "你当前还安装了以下技能（位于 .agents/skills/ 下）：",
        ]
        for s in skills:
            desc = (s["description"] or "").strip().replace("\n", " ")
            lines.append(f"- `{s['name']}`：{desc}")
        lines += [
            "使用规则（重要）：",
            "1. 只要用户的需求命中某个技能的用途（例如做 PPT/幻灯片/演示网页），必须**先**调用 "
            "loadSkill(name='技能名') 读完整份 SKILL.md，再严格按里面的步骤执行；",
            "2. 禁止直接打开技能目录下 templates / examples / 示例 HTML 当作结果返回——那些是空模板，"
            "里面的 [必填] 占位文字必须按用户真实内容替换掉；",
            "3. 技能里要跑的脚本、要读的参考文件，用 loadSkill(name, filename='相对路径') 读取。",
        ]
        content += "\n" + "\n".join(lines)
    return content


# ---------- MCP：后台异步连接，边栏动态刷新 ----------
import asyncio


def _mcp_servers():
    """从 config.ini 读 [mcp.名字] 段，返回 {名字: {"command":..,"args":[..]}}"""
    if not config:
        return {}
    servers = {}
    for section in config.sections():
        if not section.startswith("mcp."):
            continue
        command = config.get(section, "command", fallback="").strip()
        if not command:
            continue
        args = [a for a in config.get(section, "args", fallback="").split() if a]
        servers[section[4:]] = {"command": command, "args": args}
    return servers


async def _mcp_list_tools(command, args):
    """stdio JSON-RPC：initialize → tools/list；返回 (工具名列表, 进程)。失败抛异常"""
    proc = await asyncio.create_subprocess_exec(
        command, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def _rpc(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP 服务器没有响应")
        return json.loads(line.decode("utf-8"))

    await _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "lagent", "version": "1.0"}}})
    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode("utf-8"))
    await proc.stdin.drain()
    resp = await _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = [t.get("name", "?") for t in (resp.get("result") or {}).get("tools") or []]
    return tools, proc


# ---------- 斜杠命令注册表 ----------
SLASH_COMMANDS = [
    ("/new", "新建会话（自动保存当前会话历史）"),
    ("/save", "手动保存当前会话"),
    ("/resume", "打开历史会话列表，↑↓ 选择、Enter 恢复、Esc 取消"),
    ("/compact", "压缩上下文，完成后弹出两栏报告面板"),
    ("/tools", "列出当前可用工具"),
    ("/help", "显示全部斜杠命令"),
    ("/quit", "退出程序"),
]


class SessionListPanel(Vertical):
    """/resume 弹出的历史会话选择面板：自带焦点，↑↓ 选、Enter 恢复、Esc 取消"""

    BLANK = True  # 容器自身不画内容，只负责布局子部件
    BINDINGS = [
        ("up", "mv_up"), ("down", "mv_down"),
        ("enter", "pick"), ("escape", "close"),
    ]

    def __init__(self, sessions, on_pick, on_close, **kwargs):
        super().__init__(**kwargs)
        self.sessions = sessions
        self.on_pick = on_pick
        self.on_close = on_close
        self.index = 0

    def compose(self):
        yield Static("选择要恢复的会话（↑↓ 移动 · Enter 恢复 · Esc 取消）", id="resume-header")
        yield Static(" ", id="resume-body")

    def on_mount(self):
        self._render()
        self.focus()

    def _render(self):
        if not self.sessions:
            self.query_one("#resume-body", Static).update("[dim]（还没有历史会话）[/]")
            return
        rows = []
        for i, s in enumerate(self.sessions):
            mark = "►" if i == self.index else " "
            rows.append(f"{mark} [b]{s['saved_at']}[/]  [{s['count']}条]  {s['title']}")
        self.query_one("#resume-body", Static).update("\n".join(rows))

    def action_mv_up(self):
        if self.sessions:
            self.index = (self.index - 1) % len(self.sessions)
            self._render()

    def action_mv_down(self):
        if self.sessions:
            self.index = (self.index + 1) % len(self.sessions)
            self._render()

    def action_pick(self):
        if self.sessions:
            self.on_pick(self.sessions[self.index]["id"])
        else:
            self.on_close()

    def action_close(self):
        self.on_close()


class ChatApp(App):
    CSS = """
    Horizontal { height: 1fr; }
    Vertical#main-col { width: 1fr; }
    Static#banner { padding: 1 1 0 1; }
    Static#status { margin: 0 1; padding: 0 1; background: $panel; }
    RichLog#log { height: 1fr; margin: 1 1 0 1; border: round $primary; padding: 0 1; }
    Static#slash-hint { height: 3; margin: 0 1; padding: 0 1; background: $boost; display: none; }
    Input { margin: 1 1 1 1; }

    /* 右侧边栏：MCP 服务器与工具 */
    Vertical#sidebar {
        width: 34;
        border-left: round $primary;
        padding: 1 1;
        background: $panel;
    }
    Static#sidebar-title { text-style: bold; }
    VerticalScroll#mcp-list { height: 1fr; margin-top: 1; }

    /* /compact 完成后的浮动报告面板（从底部升起，不挡输入） */
    Vertical#compact-float {
        display: none;
        dock: bottom;
        height: 40%;
        border: round $accent;
        background: $panel;
        padding: 1 2;
        layout: vertical;
    }
    Vertical#compact-panel { height: 100%; }
    Static#compact-title { height: 3; margin-bottom: 1; }
    Horizontal#compact-cols { height: 1fr; }
    Vertical#compact-left { width: 1fr; padding-right: 1; }
    Vertical#compact-right { width: 1fr; padding-left: 1; }
    Static.compact-head { height: 1; }
    VerticalScroll {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        background: $surface;
    }
    Button#compact-close { margin-top: 1; }

    /* /resume 历史会话选择面板 */
    Vertical#resume-float {
        display: none;
        dock: bottom;
        height: 40%;
        border: round $accent;
        background: $panel;
        padding: 1 2;
    }
    Static#resume-header { height: 1; margin-bottom: 1; }
    Static#resume-body { height: 1fr; }
    Vertical#resume-panel { height: 100%; }
    """

    BINDINGS = [("ctrl+c", "quit", "退出"),
                 ("escape", "dismiss_compact_report", "关闭压缩报告")]

    def __init__(self, messages, window_tokens=None, window_source="默认", **kwargs):
        super().__init__(**kwargs)
        self.messages = messages
        # 上下文窗口：拿不到真实值就按 128k，并在界面上标注「默认」
        self.window_tokens = int(window_tokens or DEFAULT_CONTEXT_WINDOW)
        self.window_source = window_source
        self.api_usage = None          # 最近一次 LLM 响应自带的 token 统计
        self.usage_mark = 0            # 该统计对应的消息条数（之后新增的消息再估算）
        self.context_blocked = False   # 上下文超阈值 → 暂停接收普通提示词
        self.tiny_window = False       # 压缩后基线仍占很大比例 → 压缩降不下去了
        self.compacting = False        # 是否处于压缩状态
        self.busy = False              # 是否正在处理上一条用户消息
        self.mcp_procs = {}            # 已连接的 MCP 进程 {名字: proc}

    def compose(self) -> ComposeResult:
        skill_count = len(discover_skills())
        show_sidebar = config.getboolean("tui", "show_sidebar", fallback=True) if config else True
        with Horizontal():
            with Vertical(id="main-col"):
                yield Static(
                    "[b green]农链溯源 Agent 已启动[/]\n"
                    "[dim]定位：农产品供应链与溯源（批次追溯 / 冷链核查 / 质检合规）[/]\n"
                    f"[dim]工作目录：{os.getcwd()}[/]\n"
                    f"[dim]发现 Skill: {skill_count} 个[/]\n"
                    f"[dim]批次档案：{AGRI_DATA_DIR}[/]\n"
                    f"[dim]上下文窗口：{self.window_tokens / 1000:.0f}k（{self.window_source}）[/]\n"
                    f"[dim]上下文归档目录：{CONTEXT_DIR}[/]\n"
                    "[dim]输入 / 看全部命令，/help 列出；Ctrl+C 退出。[/]",
                    id="banner",
                )
                yield Static("", id="status")
                yield RichLog(id="log", wrap=True, markup=True, auto_scroll=True)
                yield Static("", id="slash-hint")
                yield Input(placeholder="输入问题后 Enter 发送", id="input")
                # /compact 完成后的浮动报告面板（默认隐藏，压缩成功时显示）
                with Vertical(id="compact-float"):
                    with Vertical(id="compact-panel"):
                        yield Static("", id="compact-title")
                        with Horizontal(id="compact-cols"):
                            with Vertical(id="compact-left"):
                                yield Static("[b]压缩前原文[/]（节选，完整内容见归档文件）", classes="compact-head")
                                with VerticalScroll(id="compact-pre"):
                                    yield Static("", id="compact-pre-text")
                            with Vertical(id="compact-right"):
                                yield Static("[b green]压缩后摘要[/]", classes="compact-head")
                                with VerticalScroll(id="compact-post"):
                                    yield Static("", id="compact-post-text")
                        yield Button("关闭（Esc）", id="compact-close", variant="primary")
                # /resume 历史会话选择面板（默认隐藏）
                with Vertical(id="resume-float"):
                    yield SessionListPanel([], lambda sid: None, lambda: None, id="resume-panel")
            if show_sidebar:
                with Vertical(id="sidebar"):
                    yield Static("[b]MCP 服务器[/]", id="sidebar-title")
                    with VerticalScroll(id="mcp-list"):
                        yield Static("[dim]加载中…[/]", id="mcp-list-text")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._refresh_status()
        # MCP 异步加载：先进入界面，加载完后动态刷新边栏
        self.run_worker(self._mcp_refresh_worker, exclusive=False)

    async def _mcp_refresh_worker(self) -> None:
        """后台逐个连接 config.ini 里的 MCP 服务器，把结果写进右侧边栏"""
        text_widget = None
        try:
            text_widget = self.query_one("#mcp-list-text", Static)
        except Exception:
            return
        servers = _mcp_servers()
        if not servers:
            text_widget.update("[dim]未配置 MCP 服务器[/]\n\n[dim]在 config.ini 加\n[mcp.名字] 段即可。[/]")
            return
        lines = []
        for name, spec in servers.items():
            lines.append(f"[b]{name}[/] [yellow]连接中…[/]")
            text_widget.update("\n".join(lines))
            try:
                tools, proc = await _mcp_list_tools(spec["command"], spec["args"])
                self.mcp_procs[name] = proc
                entry = f"[b]{name}[/] [green]已连接[/]\n  tools: " + (
                    ", ".join(tools) if tools else "[dim]（无）[/]")
            except Exception as e:
                entry = f"[b]{name}[/] [red]连接失败[/]\n  [dim]{str(e)[:80]}[/]"
            lines[-1] = entry
            text_widget.update("\n".join(lines))

    # ---------- 上下文占用显示与检查 ----------
    def _current_tokens(self) -> int:
        """优先用模型 API 返回的 usage，再叠加其后新增消息的估算值"""
        base = int((self.api_usage or {}).get("total_tokens") or 0)
        if base:
            return base + messages_token_estimate(
                self.messages[self.usage_mark:], include_tools=False)
        return messages_token_estimate(self.messages)

    def _record_usage(self, usage) -> None:
        if usage and usage.get("total_tokens"):
            self.api_usage = usage
            self.usage_mark = len(self.messages)

    def _refresh_status(self) -> float:
        """刷新顶部状态条：当前上下文 k / 窗口 k · 占用百分比。返回占用比例"""
        tokens = self._current_tokens()
        ratio = tokens / self.window_tokens if self.window_tokens else 0.0
        label = "（默认）" if self.window_source == "默认" else ""
        color = "green" if ratio < 0.5 else ("yellow" if ratio < CONTEXT_WARN_RATIO else "red")
        line = (f"[{color}]上下文 {tokens / 1000:.1f}k / {self.window_tokens / 1000:.0f}k"
                f"{label} · 已占用 {ratio * 100:.1f}%[/]")
        if self.compacting:
            line = "[b yellow]⏳ 上下文压缩中：暂停工具调用与新任务[/]  " + line
        elif self.context_blocked:
            line = "[b red]⚠ 已达阈值，已暂停新提示词，请输入 /compact[/]  " + line
        elif self.tiny_window:
            line = "[b yellow]窗口相对摘要偏小：压缩已到底，改用 95% 硬上限[/]  " + line
        self.query_one("#status", Static).update(line)

        inp = self.query_one(Input)
        if self.compacting:
            inp.placeholder = "正在压缩上下文，请稍候…"
        elif self.context_blocked:
            inp.placeholder = "上下文已达阈值：输入 /compact 压缩后继续"
        else:
            inp.placeholder = "输入问题后 Enter 发送"
        return ratio

    def _sync_status(self, warn=False) -> None:
        """刷新状态条；warn=True 时，首次越过阈值就告警并暂停输入（只在 App 线程执行）"""
        ratio = self._refresh_status()
        # 窗口相对摘要太小（压缩后基线仍很高）时，70% 永远回不去，改用 95% 硬上限
        limit = CONTEXT_HARD_RATIO if self.tiny_window else CONTEXT_WARN_RATIO
        if not warn or ratio < limit or self.context_blocked:
            return
        self.context_blocked = True
        self._refresh_status()
        tokens = self._current_tokens()
        log = self.query_one(RichLog)
        log.write(f"[b red]⚠ 上下文已占用 {ratio * 100:.1f}%"
                  f"（{tokens / 1000:.1f}k / {self.window_tokens / 1000:.0f}k），"
                  f"达到 {int(limit * 100)}% 告警阈值。[/]")
        log.write("[red]已暂停接收新的普通提示词，请执行 [b]/compact[/b] 压缩上下文后再继续"
                  "（原始记录会先完整归档）。[/]")

    def _finish_handle(self) -> None:
        self.busy = False
        self._refresh_status()

    def _check_context(self) -> None:
        """从工作线程触发「刷新状态条 + 阈值检查」；UI 侧出错不应影响对话流程"""
        try:
            self.call_from_thread(self._sync_status, True)
        except Exception:
            pass

    # ---------- 输入处理 ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if not q:
            return
        inp = self.query_one(Input)
        inp.clear()
        log = self.query_one(RichLog)
        self.query_one("#slash-hint", Static).display = False

        # 斜杠命令优先分发；返回 True 表示已处理，不再走对话流程
        if self._handle_slash(q):
            return

        if self.compacting:
            log.write("[yellow]正在压缩上下文，暂不接受新提示词，请稍候…[/]")
            return
        if self.context_blocked:
            log.write(f"[red]⚠ 上下文已达 {int(CONTEXT_WARN_RATIO * 100)}% 阈值，"
                      "已暂停接收新提示词。请执行 /compact 压缩上下文后再继续。[/]")
            return
        if self.busy:
            log.write("[yellow]上一条消息还在处理中，请稍候…[/]")
            return

        log.write(f"[b cyan]你[/]\n{q}")
        self.busy = True
        from functools import partial
        self.run_worker(partial(self._handle, q), thread=True)

    # ---------- 斜杠命令 ----------
    def on_input_changed(self, event: Input.Changed) -> None:
        """输入以 / 开头时弹出命令补全引导"""
        hint = self.query_one("#slash-hint", Static)
        v = event.value.strip()
        if not v.startswith("/") or " " in v:
            hint.display = False
            return
        matches = [f"[b]{c}[/] — {d}" for c, d in SLASH_COMMANDS if c.startswith(v.split()[0])]
        if not matches:
            hint.update(f"[red]未知命令 {v}，[/dim]输入 /help 查看全部")
        else:
            hint.update("　".join(matches[:4]))
        hint.display = True

    def _handle_slash(self, q: str) -> bool:
        """分发斜杠命令；已处理返回 True，否则 False"""
        log = self.query_one(RichLog)
        cmd = q.split()[0].lower()

        if cmd == "/compact":
            if self.compacting:
                log.write("[yellow]已经在压缩上下文，请稍候…[/]")
                return True
            if self.busy:
                log.write("[yellow]当前任务还在处理中，等它结束后再执行 /compact[/]")
                return True
            log.write("[b cyan]你[/]\n/compact")
            self.compacting = True
            self._refresh_status()
            self.run_worker(self._compact_worker, thread=True)
            return True

        if cmd == "/new":
            self._cmd_new()
            return True
        if cmd == "/save":
            self._cmd_save()
            return True
        if cmd == "/resume":
            self._cmd_resume()
            return True
        if cmd == "/quit":
            self.exit()
            return True
        if cmd == "/help":
            log.write("[b cyan]你[/]\n/help")
            log.write("[b]可用斜杠命令：[/]")
            for c, d in SLASH_COMMANDS:
                log.write(f"  [b]{c}[/]　{d}")
            return True
        if cmd == "/tools":
            log.write("[b cyan]你[/]\n/tools")
            for t in TOOLS:
                fn = t["function"]
                log.write(f"  [b]{fn['name']}[/]　{fn['description'][:60]}")
            return True

        if q.startswith("/"):
            log.write(f"[red]未知命令：{q.split()[0]}[/]，输入 /help 查看全部命令")
            return True
        return False

    def _cmd_save(self) -> None:
        log = self.query_one(RichLog)
        sid, path, err = save_session(self.messages)
        if err:
            log.write(f"[yellow]/save：{err}[/]")
        else:
            log.write(f"[b green]✓ 会话已保存：{path.name}（{sid}）[/]")

    def _cmd_new(self) -> None:
        log = self.query_one(RichLog)
        if self.busy or self.compacting:
            log.write("[yellow]当前任务还在处理，结束后再 /new[/]")
            return
        # 有对话内容时自动存档
        if len(self.messages) > 1:
            sid, path, err = save_session(self.messages)
            if err:
                log.write(f"[yellow]自动存档失败：{err}（仍继续开新会话）[/]")
            else:
                log.write(f"[green]原会话已自动存档：{path.name}[/]")
        # 开新会话：重建 system prompt，并按需重新注入 AGENTS.md
        self.messages[:] = [{"role": "system", "content": build_system_prompt()}]
        agents = agents_md_message()
        if agents:
            self.messages.append(agents)
            log.write("[dim]已注入当前目录 AGENTS.md[/]")
        self.api_usage = None
        self.usage_mark = 0
        self.context_blocked = False
        self.tiny_window = False
        self._hide_compact_report()
        log.write("[b green]✓ 已新建会话，可以开始提问[/]")
        self._refresh_status()

    def _cmd_resume(self) -> None:
        log = self.query_one(RichLog)
        if self.busy or self.compacting:
            log.write("[yellow]当前任务还在处理，结束后再 /resume[/]")
            return
        sessions = list_sessions()

        def on_pick(sid):
            msgs, err = load_session(sid)
            self._close_resume_panel()
            if err:
                log.write(f"[red]恢复失败：{err}[/]")
                return
            self.messages[:] = msgs
            self.api_usage = None
            self.usage_mark = 0
            self.context_blocked = False
            log.write(f"[b green]✓ 已恢复会话 {sid}（{len(msgs)} 条消息）[/]")
            self._refresh_status()
            self.query_one(Input).focus()

        def on_close():
            self._close_resume_panel()
            self.query_one(Input).focus()

        panel = self.query_one("#resume-panel", SessionListPanel)
        panel.sessions = sessions
        panel.index = 0
        panel.on_pick = on_pick
        panel.on_close = on_close
        panel._render()
        self.query_one("#resume-float").display = True
        panel.focus()

    def _close_resume_panel(self) -> None:
        try:
            self.query_one("#resume-float").display = False
        except Exception:
            pass

    # ---------- 正常对话 ----------
    def _handle(self, question: str) -> None:
        log = self.query_one(RichLog)

        def w(text: str) -> None:
            self.call_from_thread(log.write, text)

        self.messages.append({"role": "user", "content": question})
        # 命中技能关键词时，自动把该技能的 SKILL.md 注入本轮上下文，
        # 避免模型直接打开 templates 下的空模板
        skill_hint = _auto_inject_skill(question)
        if skill_hint:
            self.messages.append({"role": "user", "content": skill_hint})
            w("[dim]（已自动注入技能说明）[/]")
        # 发送新提示词后先检查一次上下文占用
        self._check_context()
        if self.context_blocked:
            self.messages.pop()
            w("[red]本条提示词未处理：上下文已达阈值，请先执行 /compact 压缩。[/]")
            self.call_from_thread(self._finish_handle)
            return

        try:
            for _ in range(10):
                chunks = []
                t0 = time.time()
                answer, calls, finish_reason, usage = stream_once(
                    self.messages,
                    on_text=lambda t: chunks.append(t),
                )
                if answer:
                    w(f"[b blue]LAgent[/]\n{answer}")
                self.messages.append(
                    {"role": "assistant", "content": answer or None, "tool_calls": calls}
                    if calls
                    else {"role": "assistant", "content": answer}
                )
                self._record_usage(usage)
                # 每次 LLM 响应后都检查一次上下文占用
                self._check_context()
                if self.context_blocked:
                    if calls:
                        # 已发出的工具调用不执行，但要补占位消息，避免历史结构不完整
                        for call in calls:
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": "（上下文已达阈值，工具调用被暂停，未执行）",
                            })
                    w("[yellow]当前任务已暂停：请执行 /compact 压缩上下文后重新提出需求。[/]")
                    break
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
        self.call_from_thread(self._finish_handle)

    # ---------- 上下文压缩 ----------
    def _compact_worker(self) -> None:
        """线程里执行一次压缩：归档 → LLM 生成摘要 → 替换上下文"""
        log = self.query_one(RichLog)

        def w(text: str) -> None:
            self.call_from_thread(log.write, text)

        w("[b yellow]开始压缩上下文（压缩期间不执行任何工具调用）…[/]")
        ok, new_messages, archive_id, err = run_compaction(
            self.messages, self.window_tokens, notify=w)
        self.call_from_thread(self._finish_compact, ok, new_messages, archive_id, err)

    def _finish_compact(self, ok, new_messages, archive_id, err) -> None:
        log = self.query_one(RichLog)
        self.compacting = False
        if ok:
            self.messages[:] = new_messages
            self.api_usage = None
            self.usage_mark = 0
            self.context_blocked = False
            tokens = messages_token_estimate(self.messages)
            ratio = tokens / self.window_tokens if self.window_tokens else 0.0
            self.tiny_window = ratio >= COMPACT_BASELINE_RATIO
            log.write(f"[b green]✓ 压缩完成：原始上下文已归档为 {archive_id}.jsonl[/]")
            log.write(f"[green]当前上下文约 {tokens / 1000:.1f}k tokens，"
                      "已解除输入限制，可以继续提问。[/]")
            if self.tiny_window:
                log.write(f"[yellow]提示：压缩后基线仍占 {ratio * 100:.1f}%，"
                          "窗口相对摘要偏小，再压缩也降不下去；"
                          f"改为在 {int(CONTEXT_HARD_RATIO * 100)}% 时才强制暂停。[/]")
            # 弹出浮动报告面板：左=压缩前原文（取自归档文件），右=压缩后摘要
            before = self._load_archive_preview(archive_id)
            after = self._extract_summary_text(new_messages)
            self.show_compact_report(before, after, archive_id)
        else:
            log.write(f"[b red]✗ 压缩失败：{err}[/]")
            log.write("[red]原有上下文保持不变，处理完问题后可再次执行 /compact。[/]")
        self.busy = False
        self._refresh_status()

    # ---------- 压缩报告浮动面板的内容准备 ----------
    def show_compact_report(self, before_text: str, after_text: str, archive_id: str) -> None:
        """填充并显示压缩结果浮动面板（浮在对话区上方，不抢输入焦点）"""
        self.query_one("#compact-title", Static).update(
            f"[b]压缩完成 · 归档 {archive_id}.jsonl[/]　"
            "[dim]左侧=压缩前原文（节选），右侧=压缩后摘要；Esc 或点按钮关闭[/]"
        )
        self.query_one("#compact-pre-text", Static).update(before_text or "（空）")
        self.query_one("#compact-post-text", Static).update(after_text or "（空）")
        self.query_one("#compact-float").display = True

    def _hide_compact_report(self) -> None:
        try:
            self.query_one("#compact-float").display = False
        except Exception:
            pass

    def action_dismiss_compact_report(self) -> None:
        self._hide_compact_report()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compact-close":
            self._hide_compact_report()

    def _load_archive_preview(self, archive_id: str) -> str:
        """读取归档 jsonl，取前 60 条消息拼成面板左栏的「压缩前原文」"""
        path = _archive_path(archive_id)
        if not path or not path.exists():
            return "（归档文件已不可读）"
        chunks = []
        try:
            with open(path, encoding="utf-8") as f:
                for i, raw in enumerate(f):
                    if i >= 60:
                        break
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    role = m.get("role", "?")
                    content = str(m.get("content") or "").strip()
                    if not content and m.get("tool_calls"):
                        names = ", ".join(
                            c.get("function", {}).get("name", "?") for c in m["tool_calls"]
                        )
                        content = f"（发起工具调用：{names}）"
                    chunks.append(f"[dim]{i + 1:>3} · {role}[/]  {content[:400]}")
        except OSError as e:
            return f"（读取归档失败：{e}）"
        return "\n\n".join(chunks)[:8000]

    def _extract_summary_text(self, new_messages) -> str:
        """压缩后新消息里最后一条 user 消息就是摘要（含归档说明）"""
        for m in reversed(new_messages or []):
            if m.get("role") == "user" and m.get("content"):
                return str(m["content"])[:8000]
        return "（未找到摘要内容）"

class FirstRunApp(App):
    """首次启动且 config.ini 缺失时：已自动生成模板，提示用户填好后重启"""

    CSS = """
    FirstRunApp { align: center middle; }
    Vertical { width: 80%; height: auto; border: round $warning; padding: 2 4; background: $panel; }
    """
    BINDINGS = [("escape", "quit"), ("enter", "quit"), ("space", "quit")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b yellow]已在当前目录生成 config.ini 模板[/]")
            yield Static("")
            yield Static(f"文件位置：{CONFIG_PATH.resolve()}")
            yield Static("请先打开它，把 [llm] 下的 api_key 填成你自己的密钥，")
            yield Static("（可选）再配好 context_window、MCP 服务器等项，")
            yield Static("修改保存后重新启动本程序即可正常对话。")
            yield Static("")
            yield Static("[dim]按 Esc / Enter / 空格 关闭本提示[/]")


def main():
    # 首次运行：刚生成了 config.ini 模板，弹窗提示后退出
    if _CONFIG_TEMPLATE_WRITTEN:
        FirstRunApp().run()
        return

    window_tokens, window_source = detect_context_window()
    messages = [{"role": "system", "content": build_system_prompt()}]
    # AGENTS.md：一次注入、全程复用（会随会话一起存档，后续轮次不再重复注入）
    agents = agents_md_message()
    if agents:
        messages.append(agents)
    ChatApp(messages, window_tokens=window_tokens, window_source=window_source).run()


if __name__ == "__main__":
    main()
