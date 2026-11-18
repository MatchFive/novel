"""P12 验收：写作链的「意图与范围」接线。

三条契约：
1. **范围解析**（``write_target`` 自由文本 → 卷区间）：只认**卷**粒度、闭区间、
   章粒度/无法解析 → **fail-open**（不限制）。
2. **意图块进写作链的 core 段**：目标/前提/限制/正文范围出现在**写作提示词**里，
   且**预算裁剪不丢**（core 段永不丢）。
3. **范围闸**：``write_plot`` 遇到范围外的卷 → **不写**（零写盘、零 LLM 调用）+ 如实上报。

LLM 全 mock，其余链路（读 brief、写盘、提示词组装）真实跑。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.run.scope import BriefScope, parse_write_range, read_brief_scope
from harness.run.state import RunState


def _settings():
    from harness.config import ProviderConfig, Settings

    return Settings(library_root=Path("x"),
                    providers={"test": ProviderConfig(base_url="https://api.test",
                                                      api_key="k", lite="m",
                                                      standard="m", pro="m")},
                    default_provider="test", default_tier="standard",
                    web_verify=True)

# ------------------------------------------------------------------ 范围解析

def test_parse_write_range_single_vol():
    """单提及：「只写/第 N 卷」→ 单点 [N,N]；「写到/写至第 N 卷」→ **累计 [1,N]**（P28）。"""
    assert parse_write_range("只写第 3 卷") == (3, 3)
    assert parse_write_range("写到第三卷") == (1, 3)
    assert parse_write_range("写至第三卷") == (1, 3)


def test_parse_write_range_double_mention_not_cumulative():
    """双提及不归累计：「第三卷写到第五卷」（无「从」）→ [3,5]，不是 [1,5]（P28）。"""
    assert parse_write_range("第三卷写到第五卷") == (3, 5)


def test_parse_write_range_two_vols_closes_interval():
    """两卷 → **闭区间**（min, max）。"""
    assert parse_write_range("从第二卷写到第五卷") == (2, 5)


def test_parse_write_range_chinese_numerals():
    """中文数字认得。"""
    assert parse_write_range("写完第十卷") == (10, 10)
    assert parse_write_range("第 12 卷") == (12, 12)


def test_parse_write_range_chapter_mentions_fail_open():
    """提到「第N章」→ **不限制**（章粒度跨卷歧义）。

    两种输入都要测：① 只有章（压根没有卷号 → 解析不出 → 不限制）；
    ② **章与卷同时出现**（「第一章到第三卷」）——否则「章粒度不限制」这条规则
    根本不会被执行（没有卷号可取，规则是空转）→ 测它等于没测。
    """
    assert parse_write_range("从第二章写到第五章") is None
    assert parse_write_range("第一章到第三卷") is None


def test_parse_write_range_unparsable_is_none():
    """什么都没提到 / 解析不出 → **不限制**（fail-open）。"""
    assert parse_write_range("随便写写") is None
    assert parse_write_range("") is None
    assert parse_write_range("第一卷") == (1, 1)


def test_scope_write_range_property():
    """``BriefScope.write_range`` 从 write_target 解析。"""
    assert BriefScope(write_target="只写第 2 卷").write_range == (2, 2)
    assert BriefScope(write_target="随便写").write_range is None


def test_scope_block_for_writing_omits_plan_scope():
    """``for_writing=True`` → **不含**「只规划前 N 卷」（那是规划链的范围）。"""
    block = BriefScope(goal="g", premise="p", constraints=["c"], plan_arcs=2,
                       write_target="只写第一卷").block(for_writing=True)
    assert "目标：g" in block
    assert "正文范围：只写第一卷" in block
    assert "只规划前 2 卷" not in block


# ------------------------------------------------------------------ 接线

def _write_brief(store, **brief):
    state = RunState.new(brief.pop("raw", "写个仙侠"), ["outline"])
    state.brief.update(brief)
    state.save(store.root)


@pytest.fixture
def project(tmp_path):
    from harness.projects import create_project
    from harness.store.project import ProjectStore

    info = create_project(tmp_path, "P12 书")
    store = ProjectStore(info.path)
    store.upsert_plots(1, [{"seq": 1, "title": "题一", "summary": "概要",
                             "conflict": "冲突", "resolution": "收束",
                             "status": "planned"}])
    store.upsert_plots(2, [{"seq": 1, "title": "题二", "summary": "概要",
                             "conflict": "冲突", "resolution": "收束",
                             "status": "planned"}])
    return info, store


async def test_write_plot_refuses_volume_out_of_range(project):
    """**范围闸**：brief 说「只写第一卷」→ 写第 2 卷**不写**（零写盘、零 LLM）。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="只写第一卷")

    calls = []

    class _LLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            calls.append(messages)
            raise AssertionError("范围外的卷不该调 LLM")

    result = await write_plot(store, _LLM(), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"})

    assert "未执行" in result.text
    assert "第 2 卷不在本次写作范围内" in result.text
    assert "第 1–1 卷" in result.text
    assert calls == []                        # 零 LLM 调用
    # 零写盘：没有任何章文件被写出来
    chapters_dir = store.root / "chapters"
    assert not chapters_dir.exists() or not list(chapters_dir.glob("*.md"))


async def test_write_plot_allows_volume_in_range(project):
    """范围内的卷**正常写**（闸不误伤）。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="从第一卷写到第二卷")

    class _LLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            raise AssertionError("本场景不该走到 LLM（章集为空）")

    result = await write_plot(store, _LLM(), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"})
    # 没被范围闸拦（落到了别的失败原因上）
    assert "不在本次写作范围内" not in result.text


async def test_write_plot_no_range_writes_normally(project):
    """没有 brief / 没有 write_target → **不限卷**（行为与 P12 前一致）。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project

    class _LLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            raise AssertionError("本场景不该走到 LLM（章集为空）")

    result = await write_plot(store, _LLM(), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"})
    assert "不在本次写作范围内" not in result.text


def test_intent_block_read_from_brief(project):
    """意图块来自 brief（目标/前提/限制/正文范围）。"""
    _info, store = project
    _write_brief(store, goal="写一部长篇仙侠", premise="少年修道成仙",
                 constraints=["每章 3000 字"], write_target="只写第一卷")

    scope = read_brief_scope(store)
    block = scope.block(for_writing=True)
    assert "目标：写一部长篇仙侠" in block
    assert "前提：少年修道成仙" in block
    assert "- 每章 3000 字" in block
    assert "正文范围：只写第一卷" in block


# ------------------------------------------------------------------ P25：范围外人确认（A2）

class _GateUI:
    """范围闸确认门假 UI：预置 indices（单选）或 cancel；记录 ask 调用。"""

    def __init__(self, indices=None, cancel=False):
        self._indices = list(indices or [])
        self._cancel = cancel
        self.asked = 0
        self.question = ""

    def ask(self, question, options, multi=False):
        from harness.run.approval import ApprovalCancelled, Choice

        assert not multi, "范围闸确认是单选"
        self.asked += 1
        self.question = question
        if self._cancel:
            raise ApprovalCancelled()
        return Choice(indices=list(self._indices))


def _no_llm(calls):
    class _LLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            calls.append(messages)
            raise AssertionError("范围闸场景不该调 LLM")

    return _LLM()


async def test_gate_out_of_range_asks_then_refuse_keeps_gate(project):
    """A2：范围外 + ui 在 → 先征求；用户拒绝 → 维持拦截（NOPROGRESS 原文案、零 LLM）。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="只写第一卷")
    calls = []
    ui = _GateUI(indices=[1])                       # 选「拒绝：维持范围拦截」

    result = await write_plot(store, _no_llm(calls), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"},
                              ui=ui)

    assert ui.asked == 1, "范围外必须先征求用户（A2）"
    assert "第 2 卷" in ui.question and "只写第一卷" in ui.question
    assert "第 2 卷不在本次写作范围内" in result.text
    assert "第 1–1 卷" in result.text               # 拒绝路径观测与 P12 逐字一致（M3 钉死）
    assert calls == []                              # 零 LLM


async def test_gate_out_of_range_cancel_keeps_gate(project):
    """审批取消（ApprovalCancelled）→ 视同拒绝，维持拦截。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="只写第一卷")
    calls = []
    ui = _GateUI(cancel=True)

    result = await write_plot(store, _no_llm(calls), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"},
                              ui=ui)

    assert ui.asked == 1
    assert "第 2 卷不在本次写作范围内" in result.text
    assert calls == []


async def test_gate_out_of_range_approve_proceeds(project):
    """用户放行 → 越过闸：落章集检查（本项目无章 → 「没有待写章节」），零 LLM。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="只写第一卷")
    calls = []
    ui = _GateUI(indices=[0])                       # 选「放行」

    result = await write_plot(store, _no_llm(calls), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "2-1"},
                              ui=ui)

    assert ui.asked == 1
    assert "不在本次写作范围内" not in result.text   # 闸已放行
    assert "没有待写章节" in result.text             # 落到章集检查（本项目无 chapters）
    assert calls == []                              # 章集为空，仍零 LLM


def test_register_writing_tools_accepts_ui(tmp_path):
    """register_writing_tools 增 ui 参透传（registry 同步边界；loop 走 await 分支）。"""
    from harness.projects import create_project
    from harness.store.project import ProjectStore
    from harness.tools.registry import ToolRegistry
    from harness.tools.writing import register_writing_tools

    info = create_project(tmp_path, "注册书")
    store = ProjectStore(info.path)
    reg = ToolRegistry(store)
    register_writing_tools(reg, store=store, llm=None, prompts=None,
                           settings=None, ui=_GateUI())
    assert "write_plot" in reg.names
    assert not reg.is_read_only("write_plot")


# ------------------------------------------------------------------ P27/A1：章粒度范围闸

def _seed_chapter(store, seq, plot_ref="1-1"):
    """种一章（planned + 一条 beat，过「无细纲」检查）。"""
    from harness.store.project import ChapterDoc

    store.write_chapter(ChapterDoc(
        seq=seq,
        meta={"seq": seq, "title": f"第{seq}章", "status": "planned",
              "plot_id": plot_ref, "beats": [{"event": "起"}]},
        body=""))


async def test_write_plot_chapter_range_all_out_is_nop(project):
    """A1 章闸：待写章**全部**不在章区间 → NOPROGRESS + 逐章 skip 行、零 LLM、零写盘。"""
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="第三章")
    _seed_chapter(store, 1)
    _seed_chapter(store, 2)
    calls = []

    result = await write_plot(store, _no_llm(calls), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "1-1"})

    first = result.text.splitlines()[0]
    assert first.startswith("NOPROGRESS: write_plot 未执行")
    assert "第 3–3 章" in first and "第三章" in first
    assert "第1章：不在本次写作范围（第 3–3 章）内，跳过" in result.text
    assert "第2章：不在本次写作范围（第 3–3 章）内，跳过" in result.text
    assert calls == []                                   # 零 LLM
    assert store.read_chapter(1).meta.get("status") == "planned"   # 越界章零写盘


async def test_write_plot_chapter_range_partial_skips_out(project):
    """A1 章闸：部分越界 → 越界章 skip 行，范围内章**照常尝试**（LLM 失败走章内失败）。"""
    from harness.llm.client import LLMError
    from harness.prompts.loader import PromptLoader
    from harness.tools.writing import write_plot

    _info, store = project
    _write_brief(store, write_target="从第二章写到第三章")
    for seq in (1, 2, 3):
        _seed_chapter(store, seq)
    calls = []

    class _FailLLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            calls.append(messages)
            raise LLMError("net down", transient=True, kind="timeout")

        async def _stream_pieces(self, messages, **kwargs):
            calls.append(messages)
            raise LLMError("net down", transient=True, kind="timeout")
            yield  # pragma: no cover - 只为成为 async generator

    result = await write_plot(store, _FailLLM(), PromptLoader(home_dir=store.root),
                              _settings(), lambda k, p: None, {"plot_id": "1-1"})

    assert "第1章：不在本次写作范围（第 2–3 章）内，跳过" in result.text
    assert calls, "范围内章（第2章起）必须真的尝试写（LLM 被调用）"
    assert "第2章：未成功" in result.text               # 范围内章照常进写作流程
    assert store.read_chapter(1).meta.get("status") == "planned"   # 越界章未动
    assert store.read_chapter(2).meta.get("status") == "planned"   # 写失败也不落盘
