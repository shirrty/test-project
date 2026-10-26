import configparser
import json
import locale
import subprocess
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("config.ini", encoding="utf-8") as f:
    config = configparser.ConfigParser()
    config.read_file(f)

base_url = config["llm"]["base_url"].rstrip("/")
api_key = config["llm"]["api_key"]
model = config["llm"]["model"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getdate",
            "description": "获取当前电脑的日期和时间",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断指定年份是不是闰年",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    },
                },
                "required": ["year"],
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


def getdate(**kwargs):
    # 调用本地 date / time 命令获取日期和时间，不使用 python 的 datetime
    proc = subprocess.run(
        "date /t & time /t",
        shell=True,
        capture_output=True,
        timeout=15,
    )
    text = decode_console(proc.stdout).strip()
    if proc.returncode != 0:
        err = decode_console(proc.stderr).strip()
        raise RuntimeError(err or "调用本地 date 命令失败")
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def isLeapYear(year=None, **kwargs):
    if year is None:
        raise ValueError("缺少参数 year")
    year = int(year)
    leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    return f"{year}年{'是' if leap else '不是'}闰年"


def call_tool(name, arguments):
    if name == "getdate":
        return getdate(**arguments)
    if name == "isLeapYear":
        return isLeapYear(**arguments)
    return f"未知的工具：{name}"


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
            if content:
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
    messages = [{"role": "system", "content": "你是一个乐于助人的助手。"}]

    while True:
        print("你好，请提出你的问题")
        question = input()
        messages.append({"role": "user", "content": question})

        for _ in range(5):
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
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                try:
                    result = call_tool(name, arguments)
                except Exception as e:
                    result = f"工具执行出错：{e}"
                print(f"[调用工具 {name}] {result}")
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"],
                     "content": str(result)}
                )


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        pass
