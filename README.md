# llm-work — LLM 应用实践合集

个人 LLM 开发实践仓库：既有基于 [MCP](https://modelcontextprotocol.io)（Model Context Protocol）
与 [AnythingLLM](https://anythingllm.io) 的知识库与工具封装，也有不依赖任何框架、
从零手写主循环的 Agent 实践。八个相互独立的小项目。

| 目录 | 说明 | 技术栈 |
| --- | --- | --- |
| [`Agent/LAgent/`](Agent/LAgent/) | **主力 Agent**：自建主循环 + 工具表 + 技能机制 + MCP 客户端 + 上下文压缩与归档检索 + 会话存档，Textual 终端界面 | Python · Textual · MCP SDK 2.x |
| [`Agent/`](Agent/) | 精简版 Agent（`main.py`）+ 课堂练习（Practice01 / Practice02 / xAgent） | Python · Textual · requests |
| [`AnythingLLMMCP/`](AnythingLLMMCP/) | 把 AnythingLLM 工作区封装成 MCP 工具，供任意 MCP 客户端提问 | Python · MCP SDK 2.x · stdio |
| [`AnythingLLMServer/`](AnythingLLMServer/) | 单文件网页：填自己的 Key，把文档上传到指定工作区并触发向量化 | 原生 HTML / JavaScript |
| [`PetHospitalMCP/`](PetHospitalMCP/) | 宠物医院病历管理 MCP 服务，本地 SQLite 持久化，含错误码与调用日志 | Python · MCP SDK 2.x · SQLite · pytest |
| [`Agent/Practice01/`](Agent/Practice01/) | 最小流式对话练习：直接调 OpenAI 兼容接口逐字输出 | Python · requests |
| [`Agent/Practice02/`](Agent/Practice02/) | 工具调用练习：日期/闰年 + 十类本地文件目录读写改删，附 Mermaid 架构图 | Python · requests |
| [`Agent/xAgent/`](Agent/xAgent/) | 联网 Agent 练习：联网搜索 + 网页正文抓取 + 本地命令执行 | Python · requests · trafilatura |

> 目录名 `AnythingLLMServer` 为历史遗留拼写（`shangchuanwangye` = 上传网页），为免破坏既有链接保留。

## 各项目的关系

```text
                  ┌──────────────────────────────┐
                  │        AnythingLLM 实例      │
                  │   workspace / 文档 / 向量库   │
                  └───▲──────────────────────▲───┘
        HTTP /api/v1│                      │HTTP /api/v1（Bearer Key）
                    │                      │
        ┌───────────┴──────────┐   ┌───────┴─────────────────┐
        │ AnythingLLMServer    │   │  AnythingLLMMCP         │
        │ 网页上传 + 向量化     │   │  8 个工具（stdio）      │
        └──────────────────────┘   └───────┬─────────────────┘
                                           │ MCP client
                                    ┌──────▼───────┐
                                    │  MCP 客户端  │
                                    └──────────────┘

   ┌───────────────┐   MCP(stdio)   ┌──────────────────┐   sqlite3   ┌──────────┐
   │  Agent/LAgent │───────────────▶│ PetHospitalMCP   │────────────▶│ SQLite   │
   │  MCP 客户端   │◀───────────────│ 8 个工具          │             │ 本地文件 │
   └───────────────┘   工具结果回填  └──────────────────┘             └──────────┘
```

- **AnythingLLMServer 与 AnythingLLMMCP** 面向同一个 AnythingLLM 实例：网页负责"把知识灌进去"，
  MCP 服务负责"把知识问出来"。两者都直连 `/api/v1`，密钥各自通过页面输入框 / 环境变量提供。
- **PetHospitalMCP 独立于 AnythingLLM**：数据落在本地 SQLite（`pet_hospital.db`），
  不调用任何外部 REST 服务，装好即可用；Agent 通过 `.mcp.json` 或 `config.ini` 的 `[mcp.xxx]` 接入。
- **Agent/LAgent** 是仓库里代码量最大的一块：主循环、工具表、技能、MCP、上下文管理全部手写，
  业务工具围绕农产品溯源（批次追溯 / 冷链审核 / 残农限量核对 / PPT 生成）。
- **Practice01 → Practice02 → xAgent** 是递进的教学线：
  最小流式闭环 → function calling 本地闭环 → 把工具扩展到互联网。

## 快速上手

各项目互相独立，按需选一个装。

### 1. Agent/LAgent（主力 Agent）

```powershell
cd Agent\LAgent
pip install textual requests trafilatura
python main.py
# 首次运行自动生成 config.ini 模板，填好 [llm] api_key 后重启即可
```

工具：`getDate` `RunCli` `searchWeb` `fetchWeb` `loadSkill` `createSkill`
`recallContext` `traceBatch` `auditColdChain` `checkResidues` `makePpt`。
斜杠命令：`/help` `/tools` `/compact` `/save` `/resume` `/new` `/quit`。

长期规则文件按 `AGENT.md` → `AGENTS.md` → `.agents/AGENT.md` 顺序查找，
命中后作为一条 user 消息注入一次，超 8000 字符截断，可用 `[agent] instructions_file` 改路径。
详见 [`Agent/README.md`](Agent/README.md)。

自测（纯逻辑，不拉起界面）：

```powershell
cd Agent
python -m unittest tests.test_agent_tools -v
```

### 2. AnythingLLMMCP

前置：本机已运行 AnythingLLM（默认 `http://localhost:3001`），并在 **Settings → API Keys** 生成密钥。
传输方式是 **stdio**（不是 HTTP 服务，没有 `uvicorn` 那一步）。

```powershell
cd AnythingLLMMCP
pip install -e ".[dev]"

# 不设 Key 会在调用工具时返回清晰错误
$env:ANYTHINGLLM_API_KEY = "<你的 API Key>"
$env:ANYTHINGLLM_API_BASE_URL = "http://localhost:3001"

python -m anythingllm_mcp.server
# 等价于安装后的控制台命令：anythingllm-mcp
```

验证客户端逻辑（用 `unittest.mock` 打桩，不需要真实 AnythingLLM）：

```powershell
pytest tests/ -v
```

接入任意 MCP 客户端（stdio 方式）：

```json
{
  "mcpServers": {
    "anythingllm": {
      "command": "python",
      "args": ["-m", "anythingllm_mcp.server"],
      "env": {
        "ANYTHINGLLM_API_KEY": "<你的 API Key>",
        "ANYTHINGLLM_API_BASE_URL": "http://localhost:3001"
      }
    }
  }
}
```

详见 [`AnythingLLMMCP/README.md`](AnythingLLMMCP/README.md)。

### 3. AnythingLLMServer

无需安装依赖，双击 `index.html`，或起个静态服务器：

```powershell
cd AnythingLLMServer
python -m http.server 8090
# 浏览器打开 http://localhost:8090/index.html
```

1. 填 **API 地址**（默认 `http://localhost:3001/api`）与 **API Key**，点「保存连接设置」——
   只写进本机浏览器 `localStorage`，页面里没有任何硬编码密钥；
2. 点「加载工作区」，列表来自 `GET /v1/workspaces`，可任选一个；
3. 选文件 → 「上传并向量化」，走 `POST /v1/document/upload` + `addToWorkspaces`。

请求过程有 **加载中 / 成功 / 失败 / 提示** 四态，失败会带上 `HTTP 状态码 + 服务端原文`。
跨源被拦时在 AnythingLLM 的 `.env` 里设 `CORS_ALLOWED_ORIGINS`。详见
[`AnythingLLMServer/README.md`](AnythingLLMServer/README.md)。

### 4. PetHospitalMCP

不依赖任何外部服务，本地 SQLite 即开即用：

```bash
cd PetHospitalMCP
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
pet-hospital-mcp            # 等价于 python -m pet_hospital_mcp.server
```

数据库路径可用环境变量 `PET_HOSPITAL_DB` 指定，默认项目根目录 `pet_hospital.db`。

运行测试（7 个用例，每个都用临时目录里的真实 SQLite，不访问网络、不依赖外部服务）：

```bash
pytest -q
```

用官方调试器验证（注意这里是 **stdio** 传输，Transport 选 stdio）：

```bash
npx @modelcontextprotocol/inspector python -m pet_hospital_mcp.server
```

返回结构固定：成功 `{"ok": true, "data": ...}`，失败
`{"ok": false, "error": ..., "detail": ..., "code": ...}`，错误码有
`NOT_FOUND` / `VALIDATION_ERROR` / `CONFLICT` / `DB_ERROR` / `INTERNAL_ERROR`。
每次调用还会输出一行 `tool= ok= code= dur=` 日志，**不记录参数值**（电话、姓名不会进日志）。
详见 [`PetHospitalMCP/README.md`](PetHospitalMCP/README.md)。

### 5. Agent/Practice01

单文件命令行对话程序：读本地 `config.ini`，流式请求 OpenAI 兼容接口，保留多轮上下文。

```powershell
cd Agent/Practice01
pip install requests
copy config.example.ini config.ini   # 填好 api_key
python chat.py
```

```ini
[llm]
base_url = https://api.siliconflow.cn/v1
api_key = <你的 API Key>
model = Qwen/Qwen3-8B
```

### 6. Agent/Practice02

两个单文件程序，共用一份 `config.ini`（格式同上）：

| 文件 | 说明 |
| --- | --- |
| `chat.py` | 对话循环 + 2 个工具：`getdate`（调用本地 `date`/`time` 命令）、`isLeapYear`（按 4/100/400 规则判定） |
| `file_chat.py` | 扩到 12 个工具：新增 `getcwd`、`list_dir`、`get_file_type`、`read_file`、`create_file`、`edit_file`、`create_dir`、`delete_dir`、`delete_file`、`rename_path` |
| `file_chat_architecture.mmd` | `file_chat.py` 的 Mermaid 架构图 |

```powershell
cd Agent/Practice02
pip install requests
python file_chat.py
```

常用示例：`现在几点`、`2024 年是不是闰年`、`在 tmp 目录创建 a.txt 写入你好并读回确认`、`把 a.txt 改名为 b.txt`。

约定：工具参数用 `json.JSONDecoder().raw_decode()` 解析，容忍流式碎片，解析失败回传模型重试；
文件操作用标准库实现，相对路径解析到当前工作目录，`delete_dir` 拒绝删除当前工作目录与磁盘根目录，
读写仅支持纯文本。请在自己的练习目录下运行。

### 7. Agent/xAgent

在 Practice02 框架上新增三个联网 / 系统工具，`config.ini` 多一个 `[search]` 小节：

| 工具 | 说明 |
| --- | --- |
| `searchWeb` | 百度千帆智能搜索生成 API，返回总结与参考来源（标题 + URL） |
| `fetchWeb` | 参数列表调 `curl` 抓页面（不用 `shell=True`），`trafilatura` 提取正文转 Markdown |
| `RunCli` | 参数列表 + `shell=False` 执行本地命令，返 stdout / stderr / 退出码，60 秒超时，超长截断 |

```powershell
cd Agent/xAgent
pip install requests trafilatura
copy config.example.ini config.ini
python chat.py
```

未配置搜索 key 时 `searchWeb` 会把缺配置的清晰错误回传模型，不影响另两个工具。

## 环境依赖一览

| 项目 | Python | 关键依赖 | 外部服务 |
| --- | --- | --- | --- |
| Agent/LAgent | 3.10+ | `textual`、`requests`、`trafilatura` | 任意 OpenAI 兼容 API、百度千帆（可选）、本机 `curl` |
| Agent（精简版 `main.py`） | 3.10+ | `textual`、`requests`、`trafilatura`、`mcp`（仅配了 `.mcp.json` 时需要） | 同上 |
| AnythingLLMMCP | 3.11+ | `mcp>=2.2.0`、`httpx`、`pydantic` | AnythingLLM（:3001） |
| AnythingLLMServer | 无（浏览器） | — | AnythingLLM（:3001） |
| PetHospitalMCP | 3.11+ | `mcp>=2.2.0`、`pydantic`、SQLite（标准库） | 无 |
| Agent/Practice01 | 3.8+ | `requests` | OpenAI 兼容 API |
| Agent/Practice02 | 3.8+ | `requests` | OpenAI 兼容 API |
| Agent/xAgent | 3.8+ | `requests`、`trafilatura`、本机 `curl` | OpenAI 兼容 API、百度千帆 |

## 安全说明

- 仓库**不包含任何 API Key**。密钥来源：`AnythingLLMMCP` 走环境变量；`AnythingLLMServer`
  走页面输入框 + 本机 `localStorage`；`Agent/*` 走本地 `config.ini`（已被 `.gitignore` 忽略）。
- `.env`、`.venv/`、`__pycache__/`、`*.egg-info/`、`*.log`、`*.db`、`config.ini`、
  `.idea/` 均已在 `.gitignore` 中排除。
- `PetHospitalMCP` 与 `AnythingLLMMCP` 都是 **stdio** 服务，由客户端拉起，
  不监听端口，本身不构成对外暴露面；`AnythingLLMServer` 是纯前端，
  AnythingLLM 的 CORS 请只放行本机来源。
- `PetHospitalMCP` 的调用日志只记 `tool` / `ok` / `code` / `dur`，**不记录任何参数值**，
  主人电话、姓名、病历内容不会进日志文件。
- `Agent/Practice02` 与 `Agent/LAgent` 会按对话指令创建、编辑、改名、删除本地文件；
  `xAgent` 与 `LAgent` 的 `RunCli` 会执行任意本地命令（教学用途，未设白名单与审批）。
  请只在自己的练习目录下运行。
- 若曾把密钥写进代码并提交过，**删除文件不会让它失效**，请到对应服务后台轮换（rotate）。

## 协议说明

`PetHospitalMCP` 与 `AnythingLLMMCP` 都使用 MCP Python SDK 2.x 的 `MCPServer`，
以 **stdio** 传输运行：由客户端作为子进程启动，进程内对话、不监听端口、每次调用独立处理、
不跨调用保存会话状态。接入方式见各自 README 的客户端配置片段。

## 文档与代码的一致性

本 README 的每条能力描述都能指到具体实现，便于复核：

| 声明 | 实现位置 |
| --- | --- |
| PetHospitalMCP 用本地 SQLite | `PetHospitalMCP/pet_hospital_mcp/db.py`（`sqlite3`，无 HTTP 客户端） |
| PetHospitalMCP 有错误码与调用日志 | `PetHospitalMCP/pet_hospital_mcp/server.py`（`E_*` 常量、`_classify`、`_traced`） |
| AnythingLLMMCP 走 stdio | `AnythingLLMMCP/anythingllm_mcp/server.py` 末尾 `mcp.run()` |
| AnythingLLMServer 无硬编码密钥 | `AnythingLLMServer/index.html` 里 Key 只来自输入框 / `localStorage` |
| Agent 十类文件操作 | `Agent/Practice02/file_chat.py`（`getcwd`…`rename_path`） |
| Agent 有技能创建 | `Agent/main.py`、`Agent/LAgent/main.py` 的 `createSkill` |
| Agent 支持 AGENT.md / AGENTS.md | `Agent/main.py:load_instructions`、`Agent/LAgent/main.py:agents_md_message` |
| Agent 有上下文压缩 | `Agent/LAgent/main.py:run_compaction` 与 `recallContext` |

## 许可证

各子项目以学习与实验为目的。`PetHospitalMCP` 在 `pyproject.toml` 中声明为 MIT。
`Agent/LAgent/.agents/skills/guizang-ppt-skill/` 为第三方技能包，按其自带 `LICENSE` 使用。
