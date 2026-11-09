# Agent —— 自建 Agent（终端交互版）

本目录是课程里"定制 Agent"的主体：一个不依赖 LangChain 的极简 Agent 框架，
主循环、工具表、技能机制、MCP 客户端、上下文压缩全部自己实现。

目录里有两套实现，**入口不同、能力不同，先看这张表再决定跑哪个**：

| 入口 | 定位 | 工具数 | 特点 |
|---|---|---|---|
| `Agent/LAgent/main.py` | 主力版本（农业溯源业务） | 11 | 上下文压缩与归档、会话存档/恢复、边栏状态、业务工具（批次追溯、冷链审核、农残核对、PPT 生成） |
| `Agent/main.py` | 精简版（通用助手） | 7 | 代码短、依赖少，适合先看懂机制再改 |
| `Agent/Practice01/` ~ `Practice02/` | 课堂练习 | 1~10 | `Practice02/file_chat.py` 是十类文件/目录操作的教学实现 |
| `Agent/xAgent/` | 练习：Web 读取与搜索 | 4 | 搜索、抓网页、命令行、技能 |

## 快速开始

```powershell
cd D:\LLM\Agent\LAgent
pip install textual requests trafilatura      # 三个第三方依赖
python main.py
```

首次运行会自动生成 `config.ini` 模板（`api_key` 是占位值），填好 Key 再启动。
`LAgent/config.example.ini` 是带注释的完整字段说明。

```ini
[llm]
base_url = https://api.deepseek.com/v1
api_key  = sk-...          # 必填
model    = deepseek-chat
context_window = 128k      # 留空则请求 /models，拿不到按 128k

[search]
baidu_qianfan_api_key =    # 不填 searchWeb 只给提示，不报错

[skills]
dir =                      # 留空 → 项目根 .agents\skills

[context]
dir =                      # 留空 → 项目根 .agents\context
warn_ratio = 0.70
hard_ratio = 0.95

[sessions]
dir = .agents/sessions

[agent]
instructions_file =        # 留空 → AGENT.md → AGENTS.md → .agents/AGENT.md
max_chars = 8000
```

## 工具表（`Agent/LAgent/main.py`）

| 工具 | 作用 | 关键行为 |
|---|---|---|
| `getDate` | 取当前日期时间 | 跨平台（`datetime`），非调用 `date /t` |
| `RunCli` | 执行本机命令 | 参数列表传参，不经 shell 拼接；有超时 |
| `searchWeb` | 网络搜索 | 百度千帆 `ai_search` 真实 POST，返回摘要 + 参考来源；未配 Key 时给提示 |
| `fetchWeb` | 读网页正文 | curl 抓取 + trafilatura 转 Markdown，校验 http(s)、30s 超时 |
| `loadSkill` | 按需加载技能 | 只允许技能目录内相对路径（`is_relative_to` 校验），输出 2000 字符截断 |
| `createSkill` | 新建技能 | 落盘"目录 + YAML frontmatter"；校验名字、单行化描述、重名拒绝覆盖、写失败回滚空目录 |
| `recallContext` | 检索已压缩的历史 | 命中 `.agents/context/*.jsonl` 归档，返回片段而不是整段回灌 |
| `traceBatch` | 批次追溯 | 读 `.agents/agri-data`，按批次号串起生产/质检/物流 |
| `auditColdChain` | 冷链审核 | 累计超温时长 + MKT，分段控温不计错 |
| `checkResidues` | 残留限量核对 | 逐项比对本地 GB2763 规则表，不用记忆里的 MRL |
| `makePpt` | 生成网页 PPT | 只需结构化内容，模板与翻页交互由工具渲染 |

精简版 `Agent/main.py` 有前 6 项中的 `getDate`、`RunCli`、`searchWeb`、`fetchWeb`、`loadSkill`、`createSkill`。

## 技能机制

技能 = 一个目录 + 一个带 YAML frontmatter 的 `SKILL.md`：

```
.agents/skills/<技能名>/
├── SKILL.md        # 必填：---\nname: ...\ndescription: ...\n---\n 正文
└── ...             # 可选：模板、脚本、参考文件
```

- **发现**：启动时扫描技能根，只把 `name + description` 注入系统提示（渐进加载，不整篇塞进上下文）。
- **加载**：模型判断需要时才 `loadSkill`，读完正文；正文超 2000 字符会截断。
- **创建**：模型调用 `createSkill` 落盘，写入前做四项校验（见工具表）。
- 技能根可用 `[skills] dir` 改；`createSkill` 只允许在技能根下建一级子目录，拒绝 `../` 之类的路径穿越。

现成示例：`.agents/skills/` 下的 `demo-skill`、`notice`、`horoscope`。

## 长期规则文件（AGENT.md / AGENTS.md）

- 查找顺序：`[agent] instructions_file` → `AGENT.md` → `AGENTS.md` → `.agents/AGENT.md`
- 命中后作为**一条 user 消息注入一次**，随会话一起存档，后续轮次不重复注入
- 超过 `[agent] max_chars`（默认 8000）会截断并标注原文长度
- 文件缺失或为空时静默跳过，不影响启动
- 本目录已放一份可用的 `AGENT.md`，写了工具使用约定、回答风格、路径规范与业务口径

## MCP 接入

两种方式都有：

1. **`.mcp.json`（精简版 `Agent/main.py`）**：支持 stdio 与 streamable-http 两种传输，
   启动时并行连接、最多等 30s；工具自动改名 `<server>__<tool>` 避免重名，
   描述前加 `[MCP:server]` 标明来源，结果回填时同样带来源。
2. **`config.ini` 的 `[mcp.名字]` 段（主力版）**：边栏显示每台 server 的连接状态与工具数。

```json
{
  "mcpServers": {
    "pet-hospital": {"command": "python", "args": ["-m", "pet_hospital_mcp.server"]}
  }
}
```

连接失败不会阻塞主流程，状态在边栏或启动日志可见。

## 上下文管理

- 用量接近 `warn_ratio` 提示，超过 `hard_ratio` 触发压缩
- `/compact` 手动压缩：先把当前消息归档成 `.agents/context/<编号>.jsonl`，
  回写校验通过后才替换上下文，失败则保持原样
- 压缩后用 `recallContext` 检索原始细节，避免"压完就忘"
- 设计取舍写在 `LAgent/上下文压缩与历史检索说明.md`

## 斜杠命令

`/help` `/tools` `/compact` `/save` `/resume` `/new` `/quit`

- `/save` 存档当前会话到 `.agents/sessions/`，`/resume` 选存档继续
- `/new` 清空上下文并重新注入 system prompt 与长期规则文件

## 自测

```powershell
cd D:\LLM\Agent
python -m unittest tests.test_agent_tools -v
```

覆盖技能创建（正常 / 重名 / 非法名 / 空描述）、frontmatter 落盘、
`discover_skills` 能否发现新技能、长期规则文件的命中顺序与截断。
缺少 `textual` 时用例自动 skip，不会报错。
