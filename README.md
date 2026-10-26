# llm-work — LLM 应用实践合集

个人 LLM 开发实践仓库，主题不再局限于 MCP：既包含基于
[MCP](https://modelcontextprotocol.io)（Model Context Protocol）与
[AnythingLLM](https://anythingllm.io) 的知识库与工具封装实验，也包含不依赖任何框架的
Agent 流式对话与工具调用入门练习，共五个相互独立的小项目：

| 目录 | 说明 | 技术栈 |
| --- | --- | --- |
| [`AnythingLLMMCP/`](AnythingLLMMCP/) | 把 AnythingLLM 工作区封装成 MCP 工具，供 opencode / 任意 MCP 客户端提问 | Python · MCP SDK 2.x · Streamable HTTP |
| [`AnythingLLMServer/`](AnythingLLMServer/) | 单文件网页工具：上传文档到 AnythingLLM 并触发向量化嵌入 | 原生 HTML / JavaScript |
| [`PetHospitalMCP/`](PetHospitalMCP/) | 把既有 Go 宠物医院 REST API 适配成无状态 MCP 服务 | Python · MCP SDK 2.x · Pydantic · pytest |
| [`Agent/Practice01/`](Agent/Practice01/) | 最小流式对话练习：直接调用 OpenAI 兼容接口逐字输出回答 | Python · requests |
| [`Agent/Practice02/`](Agent/Practice02/) | 工具调用（function calling）练习：日期/闰年计算 + 本地文件目录读写改删，附 Mermaid 架构图 | Python · requests |

> 目录名 `AnythingLLMServer` 为历史遗留拼写（`shangchuanwangye` = 上传网页），
> 为避免破坏既有链接予以保留。

## 各项目的关系

```text
                        ┌──────────────────────────────┐
                        │      AnythingLLM 实例        │
                        │  workspace / 文档 / 向量库   │
                        └───────▲──────────────▲───────┘
                                │              │
                HTTP  /api/v1/...│              │ Streamable HTTP /mcp
                                │              │
        ┌───────────────────────┴──┐        ┌──┴──────────────────────┐
        │  上传网页（浏览器直连）  │        │  AnythingLLMMCP       │
        │  document/upload        │        │  ask_workspace 工具    │
        │  workspace/update-...   │        └──┬──────────────────────┘
        └──────────────────────────┘           │ MCP client
                                                │
                                          ┌─────▼──────────┐
                                          │  opencode /    │
                                          │  其他 MCP 客户端│
                                          └────────────────┘

        ┌────────────────┐   HTTP    ┌──────────────────────┐   MCP    ┌──────────────┐
        │  AI 客户端     │──────────▶│  PetHospitalMCP    │◀─────────│  Go 宠物医院 │
        └────────────────┘  /mcp     │  list_pets 工具      │  /mcp    │  REST API    │
                                       └──────────────────────┘           └──────────────┘
```

- **AnythingLLMMCP** 与 **上传网页** 面向同一个 AnythingLLM 实例：网页负责「把知识灌进去」，
  MCP 服务负责「把知识问出来」。
- **PetHospitalMCP** 与 AnythingLLM 无关，是另一条独立练习线：把已有 REST 后端
  无侵入地包装成 MCP 工具，**不修改 Go 后端任何代码**。
- **Agent/Practice01** 同样与 MCP 无关，是最入门的一条线：绕开所有框架，
  直接用 `requests` 流式调用 OpenAI 兼容接口，理解「请求 → SSE → 逐字输出」的最小闭环。
- **Agent/Practice02** 在 Practice01 的基础上引入 **function calling**：模型按需调用
  `getdate`（执行本地 `date` / `time` 命令）、`isLeapYear` 与 10 个本地文件工具，
  本地执行结果回填 `tool` 消息后继续对话，是「模型 → 工具 → 模型」的最小闭环。

## 快速上手

五个项目互相独立，按需选择其一安装。

### 1. AnythingLLMMCP

前置条件：本机已运行 AnythingLLM（默认 `http://localhost:3001`），
并已在其界面 **Settings → API Keys** 中生成密钥。

```powershell
cd AnythingLLMMCP
pip install -r requirements.txt

# 必须设置 API Key，否则服务拒绝启动
$env:ANYTHINGLLM_API_KEY = "<你的 API Key>"

python -m uvicorn server:app --host 127.0.0.1 --port 7000
```

然后在另一个终端验证：

```powershell
python test_client.py
```

接入 opencode：本目录已附带 `opencode.json`（项目级 MCP 配置，只在当前目录生效）。
注意必须**先启动服务**，opencode 才能看到 `ask_workspace` 工具。

详见 [`AnythingLLMMCP/README.md`](AnythingLLMMCP/README.md)。

### 2. AnythingLLMServer

无需安装依赖，直接用浏览器打开 `index.html`：

1. 在页面顶部「API Key」输入框粘贴密钥（仅保存在本机浏览器的 localStorage）；
2. 页面会自动读取第一个 workspace；
3. 选择文件 → 点击「上传并嵌入」，完成上传与向量化。

若 AnythingLLM 的端口不是默认的 `50368`，在地址栏附加参数打开：
`index.html?api=http://localhost:<端口>/api`。

### 3. PetHospitalMCP

前置条件：本机已运行 Go 宠物医院 REST API（默认 `http://127.0.0.1:8080`），
且 `GET /health` 可访问。

```bash
cd PetHospitalMCP

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"

# 启动 MCP 服务（默认 127.0.0.1:8000）
PetHospitalMCP
# 等价于 python -m pet_hospital_mcp
```

运行测试（全部使用 MockTransport，**不会访问真实 Go 服务**）：

```bash
pytest -q
```

预期 `53 passed`。也可以用官方调试器验证：

```bash
npx @modelcontextprotocol/inspector
# Transport 选 Streamable HTTP，URL 填 http://127.0.0.1:8000/mcp
```

详见 [`PetHospitalMCP/README.md`](PetHospitalMCP/README.md)
与面向后续开发者的
[`UPGRADE_PROMPT.md`](PetHospitalMCP/UPGRADE_PROMPT.md)。

### 4. Agent/Practice01

一个单文件命令行对话程序：读取本地 `config.ini`，流式请求 OpenAI 兼容接口，
按 token 打印回答并保留多轮上下文。

```powershell
cd Agent/Practice01
pip install requests

# 复制下面的 config.ini 并填入你自己的 key（该文件已被 .gitignore 忽略，不会上传）
python chat.py
```

`config.ini` 格式：

```ini
[llm]
base_url = https://api.siliconflow.cn/v1
api_key = <你的 API Key>
model = Qwen/Qwen3-8B
```

启动后按提示输入问题，输入 `Ctrl+C` 结束程序。

### 5. Agent/Practice02

同目录下有两个单文件程序，共用一份 `config.ini`（格式同上，同样被忽略、不上传）：

| 文件 | 说明 |
| --- | --- |
| `chat.py` | Practice01 的对话循环 + 2 个工具：`getdate`（调用本地 `date` / `time` 命令取时间）、`isLeapYear`（python 判定闰年） |
| `file_chat.py` | 扩展到 12 个工具：在上面基础上新增 `getcwd`、`list_dir`、`get_file_type`、`read_file`、`create_file`、`edit_file`、`create_dir`、`delete_dir`、`delete_file`、`rename_path`，覆盖本地文件与目录的查看、创建、编辑、改名、删除 |
| `file_chat_architecture.mmd` | `file_chat.py` 的 Mermaid 架构图（纯文本，可直接预览渲染） |

```powershell
cd Agent/Practice02
pip install requests
python file_chat.py
```

常用示例：`现在几点`、`2024 年是不是闰年`、`在 tmp 目录创建 a.txt 写入你好并读回确认`、
`把 a.txt 改名为 b.txt`。

程序侧的两点约定：

- 工具参数用 `json.JSONDecoder().raw_decode()` 解析，容忍流式返回里 `arguments`
  的尾部碎片，解析失败会把错误回传给模型重试。
- 文件操作用 python 标准库实现，相对路径统一解析到当前工作目录；
  `delete_dir` 拒绝删除当前工作目录与磁盘根目录；读写仅支持纯文本文件。
  请在自己的练习目录下运行，避免让模型误删重要数据。

## 环境依赖一览

| 项目 | Python | 关键依赖 | 外部服务 |
| --- | --- | --- | --- |
| AnythingLLMMCP | 3.11+ | `mcp[cli]>=2.0`、`httpx`、`uvicorn` | AnythingLLM（:3001） |
| 上传网页 | 无（浏览器） | — | AnythingLLM（:50368） |
| PetHospitalMCP | 3.11+ | `mcp==2.0.0`、`httpx`、`pydantic` | Go 宠物医院 API（:8080） |
| Agent/Practice01 | 3.8+ | `requests` | SiliconFlow 等 OpenAI 兼容 API |
| Agent/Practice02 | 3.8+ | `requests` | SiliconFlow 等 OpenAI 兼容 API |

## 安全说明

- 仓库**不包含任何 API Key**。密钥一律通过环境变量（`AnythingLLMMCP`）、
  页面输入框（上传网页）或本地 `config.ini`（`Agent/Practice01`、`Agent/Practice02`，均已被忽略）提供。
- `.env`、`.venv/`、`__pycache__/`、`*.egg-info/`、`*.log`、`config.ini`
  均已在 `.gitignore` 中排除。
- 前三个项目的本地服务均**只监听 127.0.0.1**，属于本机教学/实验用途：
  `PetHospitalMCP` 明确不做认证、权限与 CORS / Origin 校验，
  **请勿直接暴露到公网**；`Agent/Practice01` 只作为出站客户端访问外部 API，
  `Agent/Practice02` 除出站请求外还会按对话指令创建、编辑、改名、删除本地文件与目录，
  请只在自己的练习目录下运行。
- `PetHospitalMCP` 的日志会对 `ownerPhone` / `ownerAddr` / `chipNo`
  （含 snake_case 写法）在任意嵌套层级递归脱敏。
- 若你曾在本仓库的历史提交中放置过密钥，请立即在对应服务中**轮换（rotate）该密钥**——
  从 Git 历史中删除并不能让它自动失效。

## 协议说明

`PetHospitalMCP` 使用 MCP **2026-07-28** 的无状态 Streamable HTTP 流程：
不实现旧版 `initialize` 握手，不使用 `Mcp-Session-Id`，无会话存储与过期机制，
每个请求独立携带 `_meta` 协议信封。详见其
[README](PetHospitalMCP/README.md#无状态说明)。

## 许可证

仓库内各子项目以学习与实验为目的。
`PetHospitalMCP` 在其 `pyproject.toml` 中声明为 MIT。
