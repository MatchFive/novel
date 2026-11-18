"""P11 集成验收：需求解析 → 规划链，范围**真的**生效。

三场景：
① **限卷**：brief 的 ``plan_arcs = 1`` + 3 卷骨架 → 逐卷情节**只规划第 1 卷**，
   第 2、3 卷未落库、观测报「按范围跳过 2 卷」；
② **范围块在 agent 覆盖之后仍在**：agent 传了 ``args.instruction`` → 范围块**仍在**提示词里；
③ **没有 brief 时行为不变**：无 state.json → 3 卷全规划、提示词里**没有**范围块。

LLM 全 mock（按提示词内容路由），规划链与需求解析真实跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.intake import run_intake
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools.plots import master_plan

INTAKE_MARK = "你是创作需求解析器"


class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage = None


class FakeLLM:
    """按提示词内容路由：需求解析 → intake 契约；否则 → 段二契约。"""

    def __init__(self, intake_payload=None):
        self.intake_payload = intake_payload
        self.calls: list[str] = []

    @property
    def provider(self):
        return ProviderConfig(base_url="https://api.test", api_key="k",
                              lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if INTAKE_MARK in prompt:
            return _Resp(self.intake_payload)
        title = "卷"
        for t in ("卷甲", "卷乙", "卷丙"):
            if f"【本卷】{t}" in prompt:
                title = t
                break
        return _Resp(json.dumps({"plots": [{"title": f"{title}的情节", "summary": "概要",
                                            "conflict": "冲突", "resolution": "收束",
                                            "beats": ["起"]}],
                                 "characters": [], "need_web": []}, ensure_ascii=False))


def _settings():
    return Settings(library_root=Path("x"),
                    providers={"test": ProviderConfig(base_url="https://api.test",
                                                      api_key="k", lite="m-lite",
                                                      standard="m-std", pro="m-pro")},
                    default_provider="test", default_tier="standard", web_verify=True)


@pytest.fixture
def project(tmp_path):
    info = create_project(tmp_path, "范围验收书")
    store = ProjectStore(info.path)
    store.upsert_arcs([
        {"vol": 1, "title": "卷甲", "goal": "g1", "summary": "s1",
         "conflict": "c1", "resolution": "r1"},
        {"vol": 2, "title": "卷乙", "goal": "g2", "summary": "s2",
         "conflict": "c2", "resolution": "r2"},
        {"vol": 3, "title": "卷丙", "goal": "g3", "summary": "s3",
         "conflict": "c3", "resolution": "r3"},
    ])
    return info, store


async def _intake(store, llm, raw="先规划第一卷"):
    """跑一次需求解析并把结果并进 brief（= REPL 的 intake 路径）。"""
    # REPL 的真实流程：先建 state（落盘）→ 跑 intake → 结果并进 brief
    state = RunState.new(raw, ["outline"])
    state.save(store.root)
    parsed, note = await run_intake(store=store, llm=llm,
                                    prompts=PromptLoader(home_dir=store.root),
                                    settings=_settings(), raw=raw,
                                    deliverables=["outline"])
    if parsed:
        state.brief.update(parsed)
        state.save(store.root)
    return parsed, note


# ------------------------------------------------------------------ 场景①

async def test_scenario_scope_limits_planning(project):
    """① 解析出「先规划第一卷」→ 规划链**只规划第 1 卷**。"""
    _info, store = project
    llm = FakeLLM(intake_payload=json.dumps({
        "goal": "写一部长篇仙侠", "premise": "少年修道成仙",
        "deliverables": ["outline"], "constraints": ["每章 3000 字"],
        "plan_arcs": 1, "write_target": "第一卷"}, ensure_ascii=False))

    parsed, _note = await _intake(store, llm)
    assert parsed is not None
    assert parsed["plan_arcs"] == 1

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               _settings(), lambda k, p: None, {})

    assert len(store.read_plots(1)) == 1          # 第 1 卷规划了
    assert store.read_plots(2) == []              # 第 2 卷没有
    assert store.read_plots(3) == []              # 第 3 卷没有
    assert "按范围跳过 2 卷" in result.text
    assert "前 1 卷" in result.text


async def test_scenario_scope_block_reaches_prompt(project):
    """① 的续：范围块**真的**进了规划提示词。"""
    _info, store = project
    llm = FakeLLM(intake_payload=json.dumps({
        "goal": "写一部长篇仙侠", "premise": "少年修道成仙",
        "deliverables": ["outline"], "constraints": ["每章 3000 字"],
        "plan_arcs": 1, "write_target": "第一卷"}, ensure_ascii=False))

    await _intake(store, llm)
    await master_plan(store, llm, PromptLoader(home_dir=store.root),
                      _settings(), lambda k, p: None, {})

    plan_prompts = [c for c in llm.calls if "【本卷】" in c]
    assert plan_prompts
    prompt = plan_prompts[0]
    assert "【本次范围与前提（来自需求解析）】" in prompt
    assert "目标：写一部长篇仙侠" in prompt
    assert "前提：少年修道成仙" in prompt
    assert "- 每章 3000 字" in prompt
    assert "范围：本次只规划前 1 卷的详细情节" in prompt
    assert "正文范围：第一卷" in prompt


# ------------------------------------------------------------------ 场景②

async def test_scenario_scope_survives_agent_instruction(project):
    """② agent 传了 instruction → 范围块**仍在**（范围是约束，不是建议）。"""
    _info, store = project
    llm = FakeLLM(intake_payload=json.dumps({
        "goal": "写一部长篇仙侠", "deliverables": ["outline"],
        "constraints": [], "plan_arcs": 1, "write_target": ""}, ensure_ascii=False))

    await _intake(store, llm)
    await master_plan(store, llm, PromptLoader(home_dir=store.root),
                      _settings(), lambda k, p: None, {"instruction": "只写仙侠"})

    prompt = next(c for c in llm.calls if "【本卷】" in c)
    assert "只写仙侠" in prompt
    assert "【本次范围与前提（来自需求解析）】" in prompt
    assert "目标：写一部长篇仙侠" in prompt


# ------------------------------------------------------------------ 场景③

async def test_scenario_without_brief_behavior_unchanged(project):
    """③ 没有 state.json → 3 卷全规划、提示词里**没有**范围块（与 P11 前一致）。"""
    _info, store = project
    llm = FakeLLM()

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               _settings(), lambda k, p: None, {})

    assert len(store.read_plots(1)) == 1
    assert len(store.read_plots(2)) == 1
    assert len(store.read_plots(3)) == 1
    assert "成功 3 卷" in result.text
    prompt = next(c for c in llm.calls if "【本卷】" in c)
    assert "本次范围与前提" not in prompt


async def test_scenario_intake_failure_keeps_planning_all(project):
    """③ 的续：需求解析失败 → brief 没范围 → 规划**不受限**（不因为解析失败就少做）。"""
    _info, store = project
    llm = FakeLLM(intake_payload="这不是 JSON")

    parsed, note = await _intake(store, llm)
    assert parsed is None
    assert "需求解析失败" in note

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               _settings(), lambda k, p: None, {})
    assert "成功 3 卷" in result.text
