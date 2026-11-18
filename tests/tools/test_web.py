"""web_search 工具（P5 Task 10）：联网核查事实。

口径钉死：
- **未启用 / 未配置 → 明确回绝**（零 LLM 零 HTTP——服务对象**根本不被构造**）；
- 成功路径观测逐条「· 核查「query」\\n 结论」+ 末尾「未能核实宁可不写」警示；
- ``parse_queries``：``;``/``；``/换行 分隔、每条 ≤60 字、最多 3 条。

服务层（真 HTTP/LLM）在 tests/test_websearch.py 覆盖；本文件用假服务钉**工具层契约**。
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from harness.config import ProviderConfig, SearchSettings, Settings
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore
from harness.tools.web import parse_queries, web_search
from harness.websearch import ResearchEntry, query_key_of


class _FakeService:
    """假 WebSearchService：记录构造次数，返回预置结论。"""

    instances: ClassVar[list[_FakeService]] = []

    def __init__(self, settings, llm, prompts, **kwargs):
        self.entries: list[ResearchEntry] = []
        self.usage_total = None
        self.closed = False
        _FakeService.instances.append(self)

    async def verify_deep(self, *, root, queries, rounds=None):
        out = []
        for query in queries:
            out.append(ResearchEntry(query=query, query_key=query_key_of(query), kind="fact",
                                     conclusion=f"已核实结论：{query}", sources=["https://x.test/1"],
                                     hit_count=1))
        self.entries = out
        return out

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_service(monkeypatch):
    _FakeService.instances = []
    monkeypatch.setattr("harness.tools.web.WebSearchService", _FakeService)
    return _FakeService


def make_settings(*, web_verify=True, search=True):
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=web_verify,
        search=(SearchSettings(provider="kimi_api", base_url="https://api.test", api_key="k")
                if search else None),
    )


@pytest.fixture
def store(tmp_path):
    from harness.projects import create_project

    info = create_project(tmp_path, "联网书")
    return ProjectStore(info.path)


async def test_web_search_disabled_refuses(store):
    """web_verify=false → 明确回绝，**零 LLM 零 HTTP**（服务对象根本不构造）。"""
    result = await web_search(store, None, PromptLoader(home_dir=store.root),
                              make_settings(web_verify=False), {"query": "率土之滨 内政"})

    assert "拒绝执行" in result.text
    assert "web_verify=false" in result.text
    assert "不得凭记忆编造" in result.text
    assert result.usage is None
    assert _FakeService.instances == []      # 零 HTTP 零 LLM


async def test_web_search_no_search_section_refuses(store):
    """未配 [search] → 明确回绝，零 LLM 零 HTTP。"""
    result = await web_search(store, None, PromptLoader(home_dir=store.root),
                              make_settings(search=False), {"query": "率土之滨 内政"})

    assert "拒绝执行" in result.text
    assert "[search]" in result.text
    assert "不得凭记忆编造" in result.text
    assert _FakeService.instances == []


async def test_web_search_observation_shape(store):
    """成功路径：逐条「· 核查「query」」+ 结论 + 末尾警示。"""
    result = await web_search(store, None, PromptLoader(home_dir=store.root),
                              make_settings(), {"query": "率土之滨 内政;文明6 尤里卡"})

    assert "· 核查「率土之滨 内政」" in result.text
    assert "已核实结论：率土之滨 内政" in result.text
    assert "· 核查「文明6 尤里卡」" in result.text
    assert "未能核实的条目宁可不写" in result.text
    assert _FakeService.instances[0].closed is True    # client 已关


async def test_web_search_bad_args(store):
    """缺 query → 参数错误文案（响亮失败）。"""
    result = await web_search(store, None, PromptLoader(home_dir=store.root),
                              make_settings(), {})

    assert "web_search 需要 args.query" in result.text
    assert _FakeService.instances == []


def test_parse_queries():
    """分隔符 / 超长截断 / limit=3。"""
    assert parse_queries("甲问题;乙问题") == ["甲问题", "乙问题"]
    assert parse_queries("甲问题；乙问题") == ["甲问题", "乙问题"]
    assert parse_queries("甲问题\n乙问题") == ["甲问题", "乙问题"]
    assert parse_queries("  甲问题  ") == ["甲问题"]
    assert parse_queries("甲;" + "乙;" + "丙;丁") == ["甲", "乙", "丙"]      # limit=3
    long = "字" * 100
    assert parse_queries(long) == ["字" * 60]                                # 每条 ≤60 字
    assert parse_queries("") == []
    assert parse_queries(";;") == []


def test_parse_queries_limit_param():
    """limit 可调（verify_deep 的 rounds 之外的条数控制）。"""
    assert parse_queries("甲;乙;丙;丁", limit=2) == ["甲", "乙"]


# ------------------------------------------------------------------ 注册（进工具表）

def _registered(store):
    """注册 web_search 进一张空 registry（工具层惯例：register_*_tools）。"""
    from harness.tools.registry import ToolRegistry
    from harness.tools.web import register_web_tools

    reg = ToolRegistry(store)
    register_web_tools(reg, store=store, llm=None, prompts=PromptLoader(home_dir=store.root),
                       settings=make_settings())
    return reg


def test_web_search_registered_in_registry(store):
    """注册：名字进 registry.names、read_only=False（写工具：写 research.md/出网/调 LLM）。"""
    reg = _registered(store)

    assert "web_search" in reg.names
    assert reg.is_read_only("web_search") is False


def test_web_search_signature_rendered_in_tool_list(store):
    """【可用工具】渲染得出 web_search 行——名字进 registry.names 才渲染得出（验收点）。"""
    from harness.agent.loop import AgentLoop
    from harness.run.approval import ApprovalUI, Choice
    from harness.run.events import EventLog
    from harness.run.memory import WorkMemory
    from harness.run.state import RunState
    from harness.run.whitelist import allowed_for_run

    reg = _registered(store)
    prompts = PromptLoader(home_dir=store.root)
    loop = AgentLoop(store=store, state=RunState.new("测试 brief", ["outline"]),
                     events=EventLog(store.root), memory=WorkMemory(), registry=reg,
                     ui=ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0])),
                     llm=None, prompts=prompts, deliverables=["outline"], settings=make_settings())

    lines = loop._tool_signatures(allowed_for_run(["outline"]))

    assert any("web_search(args: {query, rounds?})" in line for line in lines)


def test_repl_components_register_web_search(tmp_path, monkeypatch):
    """RunSession.components 组装 registry 时注册 web_search（否则 agent 永远调不到）。"""
    from harness.projects import create_project
    from harness.session import RunSession

    info = create_project(tmp_path, "联网书")
    store = ProjectStore(info.path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    registry = RunSession(info, store, make_settings()).components().registry

    assert "web_search" in registry.names
    assert registry.is_read_only("web_search") is False
