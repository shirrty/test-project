import configparser
import json
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

messages = [{"role": "system", "content": "你是一个乐于助人的助手。"}]

while True:
    print("你好，请提出你的问题")
    question = input()
    messages.append({"role": "user", "content": question})

    print("……")
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
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
                content = json.loads(data)["choices"][0]["delta"].get("content")
                if not content:
                    continue
                print(content, end="", flush=True)
                answer += content
                time.sleep(0.02)
            if done:
                break
        print()
        messages.append({"role": "assistant", "content": answer})
    except Exception as e:
        print(f"请求出错：{e}")
