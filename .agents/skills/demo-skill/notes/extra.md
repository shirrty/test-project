# 附加说明（extra.md）

这是 demo-skill 的附加文件，用来验证：
- `loadSkill(name="demo-skill", filename="notes/extra.md")` 能读取到它；
- 越界路径（如 `../../config.ini`）会被拒绝，读不到技能目录之外的文件。
