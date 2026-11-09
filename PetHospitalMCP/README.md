# 宠爱AI病历管理系统 (PetHospitalMCP)

基于 MCP（Model Context Protocol）协议的宠物医院病历管理工具集。让 AI 助手通过 MCP 调用工具来登记宠物、创建与查询病历。

数据放在**本地 SQLite**（`pet_hospital.db`），不依赖任何外部服务，启动即可用。

## 功能

- 宠物登记 / 查询 / 列表
- 病历创建 / 查询 / 更新 / 删除
- 按宠物 ID、日期范围过滤病历
- SQLite 持久化，幂等建表
- 每次工具调用输出一行结构化日志（工具名、耗时、ok、错误码）

## 环境要求

- Python 3.11+
- MCP Python SDK 2.x（`mcp` 包）

## 安装

```powershell
cd D:\PetHospitalMCP
pip install -e .
```

## 启动 MCP Server

```powershell
python -m pet_hospital_mcp.server
```

Server 以 stdio 传输运行，供 MCP 客户端（如 Claude Desktop、AnythingLLM、Cursor 等）接入。

## MCP 工具一览

| 工具 | 参数 | 功能 |
|---|---|---|
| `create_pet` | name, species, owner_name, owner_phone, breed?, age? | 登记宠物 |
| `get_pet` | pet_id | 查询宠物 |
| `list_pets` | owner_name? | 列出宠物（可按主人过滤） |
| `create_medical_record` | pet_id, visit_date, symptoms, diagnosis?, prescription?, doctor? | 创建病历 |
| `get_medical_record` | record_id | 查询病历 |
| `list_medical_records` | pet_id?, date_from?, date_to? | 列出病历 |
| `update_medical_record` | record_id, 其余字段可选 | 更新病历 |
| `delete_medical_record` | record_id | 删除病历 |

## 返回结构与错误码

成功：

```json
{"ok": true, "data": {...页面记录...}}
```

失败（四个字段固定，`code` 可程序化分支，不必匹配中文文案）：

```json
{"ok": false, "error": "未找到宠物", "detail": "pet_id=999 不存在", "code": "NOT_FOUND"}
```

| 错误码 | 触发场景 | 建议处理 |
|---|---|---|
| `NOT_FOUND` | `pet_id` / `record_id` 不存在；给不存在的宠物建病历 | 换 ID 或先 `create_pet` |
| `VALIDATION_ERROR` | 参数类型 / 必填项不对 | 修参数后重试 |
| `CONFLICT` | 唯一约束冲突等 `sqlite3.IntegrityError` | 换主键或先查询 |
| `DB_ERROR` | 其余 SQLite 异常（锁、磁盘、路径） | 检查 `PET_HOSPITAL_DB` 与文件权限 |
| `INTERNAL_ERROR` | 未分类异常 | 看 `detail` 与服务端日志 |

## 日志

每次调用由 `_traced` 装饰器记一行，**只记元信息，不记参数值**——
`owner_phone`、`owner_name` 这类个人信息不会进日志：

```
INFO pet_hospital_mcp tool=create_pet ok=True code=- dur=34ms
INFO pet_hospital_mcp tool=get_pet ok=False code=NOT_FOUND dur=0ms
```

默认由宿主进程的 logging 配置接管；想单独看日志可在启动前加：

```python
import logging; logging.basicConfig(level=logging.INFO)
```

## 配置

可通过环境变量 `PET_HOSPITAL_DB` 指定数据库文件路径，默认为项目根目录下的 `pet_hospital.db`。

## 测试

```powershell
pytest tests/ -v
```

共 7 个用例，全部基于临时目录里的真实 SQLite 文件，不访问网络、不依赖外部服务。

## 在 MCP 客户端中接入

以 Claude Desktop 的 `claude_desktop_config.json` 为例：

```json
{
  "mcpServers": {
    "pet-hospital": {
      "command": "python",
      "args": ["-m", "pet_hospital_mcp.server"],
      "cwd": "D:\\PetHospitalMCP"
    }
  }
}
```

## 架构说明

- **无状态**：每次工具调用独立处理，不跨调用保持会话
- **分层**：`server.py`（MCP 工具 + 错误码 + 调用日志）→ `store.py`（业务逻辑）→ `db.py`（连接管理）→ SQLite
- **并发安全**：每个数据库操作独立连接、独立提交、独立关闭，`busy_timeout=30s`
- **错误处理**：工具内部捕获异常，返回 `{"ok": false, "error": ..., "detail": ..., "code": ...}`
- **可观测**：`_traced` 装饰器输出 `tool= ok= code= dur=` 一行日志，且不记录参数值
