---
name: demo-skill
description: 演示技能，验证 loadSkill 的动态加载流程：被加载后会返回本文件全文和 notes 目录下的附加说明
---

# Demo Skill

这是一个示例技能，用来验证 xAgent 的 Skill 动态加载：

1. 初始化时只会把上面这一段 name / description 告诉模型。
2. 模型需要细节时调用 `loadSkill(name="demo-skill")`，才会读到这里的完整正文。
3. 调用 `loadSkill(name="demo-skill", filename="notes/extra.md")` 可以读取附加文件。

正文里写什么都行——这就是技能的完整说明书。
