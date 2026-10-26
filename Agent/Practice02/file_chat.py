import configparser
import json
import locale
import mimetypes
import os
import shutil
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

STR_PATH = {"type": "string", "description": "路径，可为绝对路径或相对当前工作目录的路径"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getdate",
            "description": "获取当前电脑的日期和时间",
            "parameters": {"type": "object", "properties": {}, "required": []},
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
                    "year": {"type": "integer", "description": "要判断的年份，例如 2024"},
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getcwd",
            "description": "获取当前工作目录",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出指定目录下所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {"path": dict(STR_PATH)},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_type",
            "description": "获取指定文件的文件类型（扩展名、MIME 类型、文本或二进制）",
            "parameters": {
                "type": "object",
                "properties": {"path": dict(STR_PATH)},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "获取指定文件的内容，仅支持纯文本格式文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": dict(STR_PATH),
                    "encoding": {"type": "string", "description": "文件编码，默认自动识别（utf-8/gbk）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "根据指定的目录和文件名创建新文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "文件所在目录，默认为当前工作目录"},
                    "filename": {"type": "string", "description": "文件名，例如 note.txt"},
                    "content": {"type": "string", "description": "文件内容，默认为空"},
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "编辑指定文件的内容，仅支持纯文本格式文件。不传 old_text 时整体覆盖，"
                          "传 old_text 时把其中的内容替换为 content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": dict(STR_PATH),
                    "content": {"type": "string", "description": "新的文件内容"},
                    "old_text": {"type": "string", "description": "要被替换的原文片段，可选"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_dir",
            "description": "创建新目录（包含多级目录）",
            "parameters": {
                "type": "object",
                "properties": {"path": dict(STR_PATH)},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_dir",
            "description": "删除目录及其下所有内容（支持非空目录）",
            "parameters": {
                "type": "object",
                "properties": {"path": dict(STR_PATH)},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除指定文件",
            "parameters": {
                "type": "object",
                "properties": {"path": dict(STR_PATH)},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_path",
            "description": "更改文件或文件夹的名字（原地改名，不移动位置）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": dict(STR_PATH),
                    "new_name": {"type": "string",
                                 "description": "新的名字，只能是文件名或目录名，"
                                                "不能包含路径分隔符，例如 report.txt 或 图片素材"},
                },
                "required": ["path", "new_name"],
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


def resolve(path=None, **kwargs):
    if not path:
        raise ValueError("缺少参数 path")
    return os.path.abspath(os.path.expanduser(str(path)))


def is_text_file(path):
    with open(path, "rb") as f:
        chunk = f.read(8192)
    return b"\x00" not in chunk


def read_text(path, encoding=None):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    if not is_text_file(path):
        raise ValueError("仅支持纯文本格式文件，该文件疑似二进制文件")
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ([encoding] if encoding else []) + ["utf-8", "gbk", locale.getpreferredencoding(False)]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError("无法识别文件编码，请指定 encoding 参数")


def getcwd(**kwargs):
    return os.getcwd()


def list_dir(path=None, **kwargs):
    target = resolve(path)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"目录不存在：{target}")
    lines = []
    for name in sorted(os.listdir(target), key=str.lower):
        full = os.path.join(target, name)
        if os.path.isdir(full):
            lines.append(f"[目录] {name}")
        else:
            lines.append(f"[文件] {name} ({os.path.getsize(full)} 字节)")
    return f"{target} 下共 {len(lines)} 项：\n" + ("\n".join(lines) if lines else "(空目录)")


def get_file_type(path=None, **kwargs):
    target = resolve(path)
    if not os.path.exists(target):
        raise FileNotFoundError(f"路径不存在：{target}")
    if os.path.isdir(target):
        return f"{target} 是目录"
    mime, encoding = mimetypes.guess_type(target)
    ext = os.path.splitext(target)[1] or "(无扩展名)"
    kind = "纯文本文件" if is_text_file(target) else "二进制文件"
    parts = [f"{target} 是{kind}", f"扩展名：{ext}", f"MIME类型：{mime or '未知'}"]
    if encoding:
        parts.append(f"压缩/编码：{encoding}")
    parts.append(f"大小：{os.path.getsize(target)} 字节")
    return "；".join(parts)


def read_file(path=None, encoding=None, **kwargs):
    target = resolve(path)
    text = read_text(target, encoding)
    return f"{target}（{len(text)} 字符）：\n{text}"


def create_file(dir=None, filename=None, content="", **kwargs):
    if not filename:
        raise ValueError("缺少参数 filename")
    folder = resolve(dir) if dir else os.getcwd()
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, str(filename))
    if os.path.exists(target):
        raise FileExistsError(f"文件已存在：{target}")
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(content or "")
    return f"已创建文件：{target}（{len(content or '')} 字符）"


def edit_file(path=None, content="", old_text=None, **kwargs):
    target = resolve(path)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"文件不存在：{target}")
    if not is_text_file(target):
        raise ValueError("仅支持纯文本格式文件，该文件疑似二进制文件")
    if old_text is None:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(content or "")
        return f"已覆盖写入 {target}（{len(content or '')} 字符）"
    text = read_text(target)
    if old_text not in text:
        raise ValueError("在文件中未找到 old_text，未做任何修改")
    text = text.replace(old_text, content or "", 1)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return f"已编辑 {target}（替换 1 处，现 {len(text)} 字符）"


def create_dir(path=None, **kwargs):
    target = resolve(path)
    if os.path.isdir(target):
        return f"目录已存在：{target}"
    os.makedirs(target, exist_ok=False)
    return f"已创建目录：{target}"


def delete_dir(path=None, **kwargs):
    target = resolve(path)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"目录不存在：{target}")
    root = os.path.splitdrive(os.path.abspath(target))[0] or os.path.sep
    if target in (os.getcwd(), os.path.abspath(root)):
        raise ValueError(f"拒绝删除当前工作目录或磁盘根目录：{target}")
    shutil.rmtree(target)
    return f"已删除目录（含其全部内容）：{target}"


def delete_file(path=None, **kwargs):
    target = resolve(path)
    if not os.path.exists(target):
        raise FileNotFoundError(f"路径不存在：{target}")
    if os.path.isdir(target):
        raise IsADirectoryError(f"这是目录而非文件，可改用 delete_dir：{target}")
    os.remove(target)
    return f"已删除文件：{target}"


def rename_path(path=None, new_name=None, **kwargs):
    if not path:
        raise ValueError("缺少参数 path")
    if not new_name:
        raise ValueError("缺少参数 new_name")
    target = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.exists(target):
        raise FileNotFoundError(f"路径不存在：{target}")
    new_name = str(new_name).strip()
    if not new_name or new_name in (".", ".."):
        raise ValueError("new_name 不能为空或为 . / ..")
    if any(s in new_name for s in ("/", "\\", ":")):
        raise ValueError("new_name 只能是文件名或目录名，不能包含路径分隔符；"
                         "本工具只改名，不移动位置")
    dest = os.path.join(os.path.dirname(target), new_name)
    if os.path.normcase(dest) == os.path.normcase(target):
        return f"名字未发生变化：{target}"
    if os.path.exists(dest):
        raise FileExistsError(f"目标名称已存在：{dest}")
    os.rename(target, dest)
    kind = "目录" if os.path.isdir(dest) else "文件"
    return f"已将{kind}重命名：{target} -> {dest}"


TOOL_HANDLERS = {
    "getdate": getdate,
    "isLeapYear": isLeapYear,
    "getcwd": getcwd,
    "list_dir": list_dir,
    "get_file_type": get_file_type,
    "read_file": read_file,
    "create_file": create_file,
    "edit_file": edit_file,
    "create_dir": create_dir,
    "delete_dir": delete_dir,
    "delete_file": delete_file,
    "rename_path": rename_path,
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
            "content": "你是一个乐于助人的助手，可以调用工具获取日期时间、判断闰年，"
                       "以及查看、创建、编辑、重命名、删除本地文件和目录。",
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
