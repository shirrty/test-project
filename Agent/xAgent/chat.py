import configparser
import json
import locale
import subprocess
import sys
import time

import requests
import trafilatura

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

# 百度千帆智能搜索生成 API（key 从 config.ini [search] 小节读取，禁止硬编码）
SEARCH_API = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
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
]


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
    # 每次调用时读取，缺配置时把清晰错误回传给模型而不是让程序崩溃
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
    # 参数列表方式调用 curl，不使用 shell=True
    try:
        proc = subprocess.run(
            # --ssl-revoke-best-effort：Windows Schannel 吊销服务器不可达时不中断抓取
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
    # 参数列表 + shell=False，避免命令注入
    cmd_list = [str(command)] + [str(a) for a in (args or [])]
    printable = " ".join(cmd_list)
    try:
        proc = subprocess.run(cmd_list, capture_output=True, timeout=60, shell=False)
    except subprocess.TimeoutExpired:
        return f"命令执行超时（60 秒）：{printable}"
    except OSError as e:
        return f"无法执行命令 {command}：{e}"
    return (f"命令：{printable}\n"
            f"退出码：{proc.returncode}\n"
            f"stdout：\n{clip_output(decode_console(proc.stdout).strip()) or '(空)'}\n"
            f"stderr：\n{clip_output(decode_console(proc.stderr).strip()) or '(空)'}")


TOOL_HANDLERS = {
    "searchWeb": searchWeb,
    "fetchWeb": fetchWeb,
    "RunCli": RunCli,
}


def call_tool(name, arguments):
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"未知的工具：{name}"
    return handler(**arguments)


def parse_arguments(raw):
    # 流式返回的 arguments 常带有尾部碎片（如 }}\n</tool_call>"}}），
    # 用 raw_decode 只取第一个完整 JSON 对象，并尝试从第一个 { 开始截取
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


def stream_once(messages):
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
                # 一旦出现 tool_calls，后续 content 是服务商混入的模板碎片，忽略
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

    print()
    return answer, [tool_calls[k] for k in sorted(tool_calls)], finish_reason


def main():
    messages = [
        {
            "role": "system",
            "content": "你是一个乐于助人的助手，可以调用 searchWeb 联网搜索实时信息、"
                       "fetchWeb 抓取网页正文、RunCli 执行本地命令行程序。"
                       "涉及最新资讯或时效性信息时先搜索再回答，"
                       "需要阅读具体网页时先抓取正文再总结。",
        }
    ]

    while True:
        print("你好，请提出你的问题")
        question = input()
        messages.append({"role": "user", "content": question})

        for _ in range(10):
            print("……")
            try:
                answer, calls, finish_reason = stream_once(messages)
            except Exception as e:
                print(f"请求出错：{e}")
                break

            messages.append(
                {"role": "assistant", "content": answer or None, "tool_calls": calls}
                if calls
                else {"role": "assistant", "content": answer}
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
                print(f"[调用工具 {name}] {clip_output(result)}")
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"],
                     "content": str(result)}
                )


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        pass
