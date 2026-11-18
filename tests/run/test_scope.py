"""P11 范围口径（纯模块）：brief → 限卷数 + 范围块文本。

口径钉死：
- ``plan_arcs`` **只认正整数**（0 = 全书 = 不限卷；负数/非数字 → 不限卷）；
- 范围块**各段只在有内容时出现**，空则**整块为空串**；
- 没有 state.json / 坏文件 → **空 scope**，不抛。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import ProjectInfo, create_project
from harness.run.scope import BriefScope, read_brief_scope
from harness.run.state import RunState
from harness.session import Components, RunSession, drive_run
from harness.store.project import ProjectStore


def _write_brief(tmp_path, brief: dict) -> None:
    root = tmp_path / ".harness"
    root.mkdir(parents=True, exist_ok=True)
    (root / "state.json").write_text(
        json.dumps({"status": "planning", "brief": brief, "stats": {"step": 0}},
                   ensure_ascii=False), encoding="utf-8")


def _store(tmp_path):
    from harness.store.project import ProjectStore

    return ProjectStore(tmp_path)


# ------------------------------------------------------------------ 读取

def test_scope_reads_brief(tmp_path):
    """brief 的四个字段都读得到。"""
    _write_brief(tmp_path, {"raw": "写个仙侠", "goal": "写一部长篇仙侠",
                             "premise": "少年修道成仙", "constraints": ["每章 3000 字"],
                             "plan_arcs": 1, "write_target": "第一卷"})
    scope = read_brief_scope(_store(tmp_path))
    assert scope.goal == "写一部长篇仙侠"
    assert scope.premise == "少年修道成仙"
    assert scope.constraints == ["每章 3000 字"]
    assert scope.plan_arcs == 1
    assert scope.write_target == "第一卷"


def test_scope_missing_state_is_empty(tmp_path):
    """没有 state.json → **空 scope**（不抛）。"""
    scope = read_brief_scope(_store(tmp_path))
    assert scope.plan_arcs is None
    assert scope.constraints == []
    assert scope.block() == ""


def test_scope_broken_state_is_empty(tmp_path):
    """坏 state.json → **空 scope**（不抛）。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    (root / "state.json").write_text("{ 不是 JSON", encoding="utf-8")
    scope = read_brief_scope(_store(tmp_path))
    assert scope.plan_arcs is None
    assert scope.block() == ""


def test_scope_plan_arcs_only_positive_int(tmp_path):
    """``plan_arcs`` **只认正整数**：0（=全书）/负数/非数字 → 不限卷。"""
    for value, expected in ((1, 1), (3, 3), (0, None), (-2, None),
                            ("abc", None), (None, None), ("2", 2)):
        _write_brief(tmp_path, {"raw": "r", "plan_arcs": value})
        scope = read_brief_scope(_store(tmp_path))
        assert scope.plan_arcs == expected, value


# ------------------------------------------------------------------ 渲染

def test_scope_block_omits_empty_sections():
    """各段**只在有内容时**出现；全空 → **空串**。"""
    assert BriefScope().block() == ""
    assert BriefScope(plan_arcs=2).block() == (
        "【本次范围与前提（来自需求解析）】\n"
        "范围：本次只规划前 2 卷的详细情节（卷骨架仍按全书产出）")
    out = BriefScope(write_target="第一卷").block()
    assert "正文范围：第一卷" in out
    assert "前提：" not in out
    assert "限制：" not in out
    # **M1**：include_volume_limit=False 时**不收**限卷行（段一用它）
    out2 = BriefScope(plan_arcs=1, goal="g").block(include_volume_limit=False)
    assert "只规划前 1 卷" not in out2
    assert "目标：g" in out2


def test_scope_block_lists_constraints():
    """限制逐行「- xxx」。"""
    out = BriefScope(constraints=["每章 3000 字", "不写系统流"]).block()
    assert "限制：" in out
    assert "- 每章 3000 字" in out
    assert "- 不写系统流" in out


def test_scope_block_has_no_plan_section_when_unlimited():
    """不限卷（plan_arcs=None）→ **不出现**「范围」段。"""
    out = BriefScope(goal="g", premise="p").block()
    assert "范围：" not in out
    # **M1**：include_volume_limit=False 时段一**不收**限卷行
    out2 = BriefScope(plan_arcs=1, goal="g").block(include_volume_limit=False)
    assert "只规划前 1 卷" not in out2
    assert "目标：g" in out
    assert "前提：p" in out


def test_scope_block_heading_is_stable():
    """块头固定（调用方按它识别范围块）。"""
    out = BriefScope(goal="g").block()
    assert out.startswith("【本次范围与前提（来自需求解析）】")


def test_scope_state_roundtrip(tmp_path):
    """RunState 存的 brief → 范围口径读得到（两模块口径一致）。"""
    state = RunState.new("写个仙侠", ["outline"])
    state.brief["goal"] = "写一部长篇仙侠"
    state.brief["plan_arcs"] = 3
    state.save(tmp_path)
    scope = read_brief_scope(_store(tmp_path))
    assert scope.goal == "写一部长篇仙侠"
    assert scope.plan_arcs == 3


# ------------------------------------------------------------------ P20：范围折算不出来时**如实说明**

def test_writing_block_reports_unfoldable_range():
    """正文范围**折算不成确定范围**（卷或章都不行）→ 写作块里**如实说明**（不拦截）。

    模块 docstring 早就承诺「由调用方**如实说明**「未能解析成确定区间 → 未限制」」，
    此前**没有人说** → 用户的显式范围被静默丢掉（写盘闸不拦、写作侧也不知道）。
    """
    out = BriefScope(write_target="不写第一卷").block(for_writing=True)
    assert "正文范围：不写第一卷" in out
    assert "无法折算成确定范围" in out
    assert "不按范围拦截" in out


def test_writing_block_reports_chapter_range():
    """A1/P27：章粒度**折算得出来** → 写作块明说「已按章拦截」（不再是「不拦截」）。"""
    out = BriefScope(write_target="从第二章写到第五章").block(for_writing=True)
    assert "正文范围：从第二章写到第五章" in out
    assert "已按章拦截：只写第 2–5 章" in out
    assert "不按范围拦截" not in out
    assert "无法折算" not in out


def test_writing_block_silent_when_range_is_deterministic():
    """范围**折算得出来** → 不加那句说明（闸会拦，模型不需要知道）。"""
    out = BriefScope(write_target="只写第一卷").block(for_writing=True)
    assert "正文范围：只写第一卷" in out
    assert "不按卷拦截" not in out


def test_planning_block_keeps_quiet_about_write_gate():
    """**规划链**的块不出现拦截说明（写盘闸是写作链的事，规划链不掺和）。"""
    out = BriefScope(write_target="从第二章写到第五章").block()
    assert "正文范围：从第二章写到第五章" in out
    assert "不按范围拦截" not in out
    assert "按章拦截" not in out


def test_writing_block_quiet_without_write_target():
    """**没有**正文范围 → 不加说明（没约束可说）。"""
    out = BriefScope(goal="g").block(for_writing=True)
    assert "不按范围拦截" not in out
    assert "正文范围" not in out


# ------------------------------------------------------------------ P27/A1：章粒度范围解析

def test_parse_chapter_range_closed_interval():
    """章区间：单提及=单点（与卷口径一致）、两章=闭区间、中文/阿拉伯数字都认。"""
    from harness.run.scope import parse_chapter_range

    assert parse_chapter_range("从第二章写到第五章") == (2, 5)
    assert parse_chapter_range("第 3 章") == (3, 3)
    assert parse_chapter_range("从第2章写到第10章") == (2, 10)


def test_parse_chapter_range_cumulative():
    """P28：「写到/写至第 N 章」→ **累计 [1,N]**；「只写第 N 章」保持单点。"""
    from harness.run.scope import parse_chapter_range

    assert parse_chapter_range("写到第五章") == (1, 5)
    assert parse_chapter_range("写至第五章") == (1, 5)
    assert parse_chapter_range("只写第五章") == (5, 5)


def test_parse_chapter_range_fail_open():
    """A1 fail-open 口径：混提/否定/开区间/无章/空 → None（与卷口径同款）。"""
    from harness.run.scope import parse_chapter_range

    assert parse_chapter_range("第一章到第三卷") is None      # 章与卷混提（旧测试钉死）
    assert parse_chapter_range("不写完第三章") is None         # 排除语义
    assert parse_chapter_range("从第三章开始写") is None        # 开区间
    assert parse_chapter_range("只写第一卷") is None           # 卷粒度走 vol 通道
    assert parse_chapter_range("") is None
    assert parse_chapter_range(None) is None


def test_scope_chapter_range_property():
    """``BriefScope.chapter_range``：章粒度解析；卷粒度/解析不出 → None。"""
    assert BriefScope(write_target="从第二章写到第五章").chapter_range == (2, 5)
    assert BriefScope(write_target="只写第一卷").chapter_range is None
    assert BriefScope(write_target="随便写").chapter_range is None


# ------------------------------------------------------------------ P22/B2：范围未折算 → **用户可见**说明


class _FakeLoop:
    """桩 AgentLoop：不触网，run() 直接返回（只验证 drive_run 在起循环**之前**的注记）。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        return None


def _drive(tmp_path, monkeypatch, *, write_target):
    """造一个项目 + run（brief 里塞 write_target），桩掉 AgentLoop 后跑 drive_run。

    返回 (notes, state)——notes 是 on_note 收到的全部行。
    """
    from harness import session as session_mod

    monkeypatch.setattr(session_mod, "AgentLoop", _FakeLoop)
    info = create_project(tmp_path, "范围说明书")
    store = ProjectStore(info.path)
    settings = Settings(library_root=tmp_path,
                        providers={"test": ProviderConfig(
                            base_url="https://api.test", api_key="k",
                            lite="m", standard="standard-model", pro="pro-model")},
                        default_provider="test", default_tier="standard",
                        web_verify=True)
    sess = RunSession(ProjectInfo(info.path, "范围说明书"), store, settings)
    state = RunState.new("写正文", ["outline"])
    if write_target is not None:
        state.brief["write_target"] = write_target
    notes: list[str] = []
    asyncio.run(drive_run(sess, state, Components(None, None, None),
                          on_note=notes.append))
    return notes, state


@pytest.mark.parametrize("write_target,reason", [
    ("不写第一卷", "排除"),          # 否定/排除语义
    ("第一章到第三卷", "混提"),      # 章与卷混提（P27/A1 统一原因）
    ("从第二卷开始写", "开区间"),      # 方向/开区间
])
def test_drive_run_notes_unfoldable_write_target(tmp_path, monkeypatch,
                                                 write_target, reason):
    """B2（独立复审建议 13）：折算不出确定范围 → on_note 给用户一行可见说明（每 run 一次）。

    提示词里的那句「如实说明」模型看得见、用户**看不见**——这里钉用户侧。
    """
    notes, _state = _drive(tmp_path, monkeypatch, write_target=write_target)
    scope_notes = [n for n in notes if "不按范围拦截" in n]
    assert len(scope_notes) == 1, notes
    assert write_target in scope_notes[0]
    assert reason in scope_notes[0]


def test_drive_run_notes_chapter_range(tmp_path, monkeypatch):
    """P27/A1：章粒度折算得出 → on_note 给**按章拦截**的 info 行（不再是「不拦截」warn）。"""
    notes, _state = _drive(tmp_path, monkeypatch, write_target="从第二章写到第五章")
    scope_notes = [n for n in notes if "按章拦截" in n]
    assert len(scope_notes) == 1, notes
    assert "从第二章写到第五章" in scope_notes[0]
    assert "第 2–5 章" in scope_notes[0]
    assert all("不按范围拦截" not in n for n in notes)


def test_drive_run_quiet_when_range_parseable(tmp_path, monkeypatch):
    """范围**折算得出来**（写盘闸会生效）→ 不刷屏，无范围说明行。"""
    notes, _state = _drive(tmp_path, monkeypatch, write_target="只写第一卷")
    assert all("不按范围拦截" not in n for n in notes)
    assert all("按章拦截" not in n for n in notes)


def test_drive_run_quiet_without_write_target(tmp_path, monkeypatch):
    """没有正文范围 → 无范围说明行。"""
    notes, _state = _drive(tmp_path, monkeypatch, write_target=None)
    assert all("不按范围拦截" not in n for n in notes)
