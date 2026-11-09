"""Agent 纯逻辑自测（不拉起 TUI、不访问网络）

跑法：
    cd D:\\LLM\\Agent
    python -m unittest tests.test_agent_tools -v

说明：被测对象是 `Agent/main.py` 里的 createSkill / load_instructions / discover_skills。
这几个函数与界面无关，缺 textual 时会自动给 TUI 依赖打桩，因此在本机与无 GUI 环境都能跑。
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
MAIN_PY = AGENT_DIR / "main.py"


def _stub(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


class _AnyMeta(type):
    """给 TUI 依赖用的宽松桩：任何属性访问都返回一个可实例化的空类"""

    def __getattr__(cls, item):
        v = _AnyMeta(item, (object,), {"__init__": lambda s, *a, **k: None})
        setattr(cls, item, v)
        return v


def _install_tui_stubs():
    """只在真的缺少 textual / rich 时打桩，装了就走真依赖"""
    try:
        import textual.app  # noqa: F401
        return False
    except ImportError:
        pass
    for n in ("textual", "textual.widgets", "textual.containers", "textual.app",
              "textual.reactive", "rich", "rich.cells", "rich.markup", "rich.text"):
        _stub(n)
    sys.modules["rich.cells"].cell_len = len
    sys.modules["rich.markup"].escape = lambda s: s
    sys.modules["rich.text"].Text = _AnyMeta("Text", (object,), {})
    for n in ("RichLog", "Input", "Static", "Button", "Label", "Select", "Checkbox"):
        setattr(sys.modules["textual.widgets"], n, _AnyMeta(n, (object,), {}))
    for n in ("Horizontal", "Vertical", "VerticalScroll", "Container", "ScrollableContainer"):
        setattr(sys.modules["textual.containers"], n, _AnyMeta(n, (object,), {}))

    class App(metaclass=_AnyMeta):
        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

    sys.modules["textual.app"].App = App
    sys.modules["textual.app"].ComposeResult = object
    sys.modules["textual.reactive"].reactive = lambda *a, **k: None
    return True


def load_agent_module():
    """按路径加载 Agent/main.py；cwd 切到临时目录，避免在仓库里生成 config.ini"""
    _install_tui_stubs()
    workdir = tempfile.mkdtemp(prefix="agent-selftest-")
    old = os.getcwd()
    os.chdir(workdir)
    try:
        spec = importlib.util.spec_from_file_location("agent_main_under_test", MAIN_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.chdir(old)


class SkillCreationTest(unittest.TestCase):
    """createSkill：落盘、校验、可被发现"""

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent_module()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-"))
        self.agent.SKILLS_DIR = self.tmp

    def test_writes_frontmatter_and_body(self):
        out = self.agent.createSkill(name="demo-weather", description="查询天气", body="# 用法\n\n调 api")
        self.assertIn("已创建技能", out)
        md = (self.tmp / "demo-weather" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("---\n"), "必须以 YAML frontmatter 开头")
        self.assertIn("name: demo-weather", md)
        self.assertIn("description: 查询天气", md)
        self.assertIn("# 用法", md)

    def test_description_is_single_line(self):
        self.agent.createSkill(name="multi-line", description="第一行\n第二行", body="正文")
        md = (self.tmp / "multi-line" / "SKILL.md").read_text(encoding="utf-8")
        desc_line = [ln for ln in md.splitlines() if ln.startswith("description:")][0]
        self.assertNotIn("\n", desc_line)

    def test_duplicate_is_rejected(self):
        self.agent.createSkill(name="dup-skill", description="第一次", body="a")
        out = self.agent.createSkill(name="dup-skill", description="第二次", body="b")
        self.assertIn("拒绝覆盖", out)
        md = (self.tmp / "dup-skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("第一次", md, "原技能内容不能被改写")

    def test_illegal_names_rejected(self):
        for bad in ("../evil", "a/b", "a\\b", "BadName", "with space", "x" * 65, ".."):
            out = self.agent.createSkill(name=bad, description="x", body="y")
            self.assertIn("不合法", out, f"{bad!r} 应当被拒绝")
        self.assertEqual([p.name for p in self.tmp.iterdir()], [], "被拒绝时不能留下目录")

    def test_missing_name_rejected(self):
        out = self.agent.createSkill(name="", description="x", body="y")
        self.assertIn("缺少参数 name", out)
        self.assertEqual([p.name for p in self.tmp.iterdir()], [])

    def test_empty_description_rejected(self):
        out = self.agent.createSkill(name="no-desc", description="   ", body="y")
        self.assertIn("description", out)
        self.assertFalse((self.tmp / "no-desc").exists())

    def test_new_skill_is_discoverable_and_loadable(self):
        self.agent.createSkill(name="trace-helper", description="批次追溯辅助", body="# 步骤\n1. traceBatch")
        names = [s["name"] for s in self.agent.discover_skills()]
        self.assertIn("trace-helper", names)
        loaded = self.agent.loadSkill(name="trace-helper")
        self.assertIn("批次追溯辅助", loaded)

    def test_create_skill_registered_as_tool(self):
        declared = [t["function"]["name"] for t in self.agent.TOOLS]
        self.assertIn("createSkill", declared, "必须在工具表里声明，否则模型调不到")
        self.assertIn("createSkill", self.agent.TOOL_HANDLERS)


class InstructionsFileTest(unittest.TestCase):
    """AGENT.md / AGENTS.md：查找顺序、截断、缺失不报错"""

    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent_module()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="instructions-"))
        self._old = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._old)

    def test_missing_returns_none(self):
        self.assertIsNone(self.agent.load_instructions())

    def test_empty_returns_none(self):
        (self.tmp / "AGENT.md").write_text("   \n\n", encoding="utf-8")
        self.assertIsNone(self.agent.load_instructions())

    def test_prefers_agent_md_over_agents_md(self):
        (self.tmp / "AGENT.md").write_text("# 新约定\n用 getDate", encoding="utf-8")
        (self.tmp / "AGENTS.md").write_text("# 旧约定", encoding="utf-8")
        text = self.agent.load_instructions()
        self.assertIn("新约定", text)
        self.assertNotIn("旧约定", text)

    def test_falls_back_to_agents_md(self):
        (self.tmp / "AGENTS.md").write_text("# 旧约定", encoding="utf-8")
        self.assertIn("旧约定", self.agent.load_instructions())

    def test_truncates_over_limit(self):
        (self.tmp / "AGENT.md").write_text("# 规则\n" + "字" * 9000, encoding="utf-8")
        text = self.agent.load_instructions()
        self.assertIn("已截断", text)
        self.assertLessEqual(len(text), self.agent.INSTRUCTIONS_MAX + 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
