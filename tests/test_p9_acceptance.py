"""P9 集成验收：清单的**生成阶段**注入（"先定标准再写"）。

契约：
① 生成侧提示词含**阶段清单块**（形态「（标题）\\n正文」+ 块头说明）；
② 块与**验收侧同一来源**（同一份清单文件：内置 → 用户级 → 项目级，同名后者赢）；
③ 块头显式写「不适用就跳过 / 不要为了凑清单而硬写设定」（seed 的历史事故防线）；
④ **提示词模板一个字不动**（块是代码生成的附加段，与 skill 注入同款做法）。

LLM 全 mock，其余链路（清单读盘、工具渲染）真实跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore

BLOCK_HEAD = "【本阶段验收清单"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """顺序吐脚本的假 llm（记录每次提示词）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        self.calls.append(messages[-1]["content"])
        return _Resp(self._script.pop(0))


def _settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m-lite", standard="m-std", pro="m-pro")},
        default_provider="test", default_tier="standard", web_verify=True)


def _arcs_payload():
    return json.dumps({"planned_volume_count": 1, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r", "capacity_hint": "约40章"}]}, ensure_ascii=False)


def _plots_payload():
    return json.dumps({"plots": [{"title": "开端", "summary": "概要", "conflict": "冲突",
                                  "resolution": "收束", "beats": ["起"]}],
                       "characters": [], "need_web": []}, ensure_ascii=False)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """一个空项目 + **隔离的用户级清单目录**。"""
    info = create_project(tmp_path, "生成侧清单书")
    store = ProjectStore(info.path)
    user_dir = tmp_path / "user_checklists"
    user_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("harness.checklists.user_checklist_dir",
                        lambda stage: user_dir / stage)
    return info, store, user_dir


def _write_checklist(base: Path, title: str, text: str) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{title}.md").write_text(text + "\n", encoding="utf-8")


# ------------------------------------------------------------------ 场景①

async def test_plan_arcs_prompt_carries_stage_checklist(project):
    """① 卷骨架生成的提示词含**内置 outline 清单块**（形态与块头都对）。"""
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    llm = FakeLLM([_arcs_payload()])

    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    prompt = llm.calls[0]
    assert BLOCK_HEAD in prompt
    # 形态：标题在圆括号里单独一行，正文紧随其后（**不是**验收侧的「N. 标题：提示」）
    assert "（情节检查-每卷清单）" in prompt
    assert "每卷检查：主线是否推进？" in prompt
    assert "（爽点密度-阶段标准）" in prompt
    # 生成侧用 outline 清单，**不含** finish 清单
    assert "情节检查-全书清单" not in prompt


async def test_plan_plots_prompt_carries_stage_checklist(project):
    """① 的续：逐卷情节规划的提示词**也**带同一份清单块。"""
    from harness.tools.plots import master_plan

    info, store, _user = project
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    llm = FakeLLM([_plots_payload()])

    await master_plan(store, llm, PromptLoader(home_dir=info.path), _settings(),
                      lambda kind, payload: None, {})

    prompt = llm.calls[0]
    assert BLOCK_HEAD in prompt
    assert "（情节检查-每卷清单）" in prompt


async def test_plan_plots_block_not_inside_html_comment(project):
    """**B-1/D1**：逐卷情节规划的清单块**不得**落进模板的 HTML 文档注释里。

    复审实测：`task.auto_plan_plots` 的注释里写着「输出契约（…）：同模板【输出格式】」，
    按「第一个【输出格式】」插位会命中**注释里那一处** → 清单块被塞进注释内部，
    且**落在角色句之前**（模型面对的是一段模板元信息，不是本次任务的标准）。
    同一个函数在两个注入点上行为不一致（`task.auto_master_plan` 只有一处命中，故正常）。
    """
    from harness.tools.plots import master_plan

    info, store, _user = project
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    llm = FakeLLM([_plots_payload()])

    await master_plan(store, llm, PromptLoader(home_dir=info.path), _settings(),
                      lambda kind, payload: None, {})

    prompt = llm.calls[0]
    # 角色句之后（块是给模型的标准，不是模板元信息）
    assert prompt.index(BLOCK_HEAD) > prompt.index("你是长篇网文的情节策划")
    # 且在**真输出契约段**之前（【输出格式】在本模板出现两次：注释里 + 真段）
    assert prompt.index(BLOCK_HEAD) < prompt.rindex("【输出格式】")
    # 块不得在 HTML 注释区间内
    comment_end = prompt.index("-->")
    assert prompt.index(BLOCK_HEAD) > comment_end


async def test_plan_plots_checklist_read_runs_in_worker_thread(project, monkeypatch):
    """**B-4**：逐卷情节规划侧的清单读盘**也**走 ``asyncio.to_thread``（P4 I-1）。

    复审盲区：此前只 spy 了卷骨架那侧（`outline_mod`），plots 侧失守无人知。
    """
    import asyncio

    from harness.tools import plots as plots_mod
    from harness.tools.plots import master_plan

    info, store, _user = project
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    state = {"in_thread": False, "read_in_thread": []}
    real_to_thread = asyncio.to_thread
    real_block = plots_mod.stage_checklist_block

    async def spy_to_thread(func, /, *args, **kwargs):
        state["in_thread"] = True
        try:
            return await real_to_thread(func, *args, **kwargs)
        finally:
            state["in_thread"] = False

    def spy_block(root, stage, **kw):
        state["read_in_thread"].append(state["in_thread"])
        return real_block(root, stage, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(plots_mod, "stage_checklist_block", spy_block)

    llm = FakeLLM([_plots_payload()])
    await master_plan(store, llm, PromptLoader(home_dir=info.path), _settings(),
                      lambda kind, payload: None, {})

    assert state["read_in_thread"] == [True]


async def test_two_forms_do_not_mix_across_sides(project):
    """**B-5**：生成侧与验收侧的**两套形态**在端到端层面也不能混。

    复审盲区：此前只在**单元层**（纯函数）断言两套形态——若有人把验收侧的
    ``render_checklist_items``（「N. 标题：提示」）误用成生成侧块，集成测试抓不到。
    """
    from harness.check.runner import run_check
    from harness.memory.store import MemoryStore
    from harness.tools.outline import plan_arcs

    info, store, _user = project

    # 生成侧
    llm = FakeLLM([_arcs_payload()])
    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})
    gen_prompt = llm.calls[0]
    assert "（情节检查-每卷清单）" in gen_prompt          # 生成侧 = 「（标题）\n正文」
    assert "1. 情节检查-每卷清单：" not in gen_prompt      # 验收侧形态不许串到生成侧

    # 验收侧
    check_llm = FakeLLM([json.dumps({"verdict": "pass", "score": 9, "issues": [],
                                      "checklist": []}, ensure_ascii=False)])
    await run_check(store=store, mem=MemoryStore(info.path), llm=check_llm,
                    prompts=PromptLoader(home_dir=info.path), settings=_settings(),
                    kind="plot", chapters=None, editor_mode=False,
                    emit=lambda k, p: None)
    check_prompt = check_llm.calls[0]
    assert "1. 情节检查-每卷清单：" in check_prompt        # 验收侧 = 「N. 标题：提示」
    assert "（情节检查-每卷清单）" not in check_prompt      # 生成侧形态不许串到验收侧


# ------------------------------------------------------------------ 场景②

async def test_project_checklist_reaches_generation_prompt(project):
    """② 项目级自定义清单条目出现在**生成侧**提示词里（不是只在验收侧）。"""
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    _write_checklist(info.path / ".harness" / "checklists" / "outline",
                     "我的清单", "本卷必须交代师门来历。")
    llm = FakeLLM([_arcs_payload()])

    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    assert "（我的清单）" in llm.calls[0]
    assert "本卷必须交代师门来历。" in llm.calls[0]


async def test_generation_and_acceptance_share_same_source(project):
    """② 的续：**同源**——项目级覆盖用户级后，生成侧与验收侧都是**项目版**。

    这是这条通道的意义：生成时看到的标准，与验收时逐条判定的靶子，出自同一份文件。
    """
    from harness.check.runner import run_check
    from harness.memory.store import MemoryStore
    from harness.tools.outline import plan_arcs

    info, store, user_dir = project
    _write_checklist(user_dir / "outline", "同名清单", "用户版提示。")
    _write_checklist(info.path / ".harness" / "checklists" / "outline",
                     "同名清单", "项目版提示。")

    llm = FakeLLM([_arcs_payload()])
    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})
    assert "项目版提示。" in llm.calls[0]
    assert "用户版提示。" not in llm.calls[0]

    # 验收侧读同一份清单 → 也是项目版
    check_llm = FakeLLM([json.dumps({"verdict": "pass", "score": 9, "issues": [],
                                      "checklist": []}, ensure_ascii=False)])
    await run_check(store=store, mem=MemoryStore(info.path), llm=check_llm,
                    prompts=PromptLoader(home_dir=info.path), settings=_settings(),
                    kind="plot", chapters=None, editor_mode=False,
                    emit=lambda k, p: None)
    assert "同名清单：项目版提示。" in check_llm.calls[0]
    assert "用户版提示。" not in check_llm.calls[0]


# ------------------------------------------------------------------ 场景③

async def test_block_header_warns_against_forcing_items(project):
    """③ 块头明写「不适用就跳过 / 不要为了凑清单而硬写设定」。

    seed 的历史事故：把「维度清单」读成「必产清单」→ 每本书都硬造一套创世神系。
    """
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    llm = FakeLLM([_arcs_payload()])

    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    prompt = llm.calls[0]
    assert "逐条核对" in prompt
    assert "不适用的就跳过" in prompt
    assert "不要为了凑清单而硬写设定" in prompt


# ------------------------------------------------------------------ 场景④

async def test_prompt_template_untouched_by_block(project):
    """④ 清单块是**附加段**：模板正文一个字不动（只往里插，不改字）。"""
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    llm = FakeLLM([_arcs_payload()])

    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    prompt = llm.calls[0]
    # 注意：模板里有 {{instruction}} 占位符 → 比的是**渲染后**的那一段
    rendered = PromptLoader(home_dir=info.path).render(
        "task.auto_master_plan", instruction="规划一卷骨架")
    head, _, tail = rendered.partition("【输出格式】")
    assert head in prompt              # 输出契约之前的模板正文，逐字保留
    assert ("【输出格式】" + tail) in prompt  # 输出契约本身，逐字保留


async def test_block_comes_before_output_contract(project):
    """④ 的续：清单块插在【输出格式】**之前**——输出契约是模型最后读的指令。

    seed 把这类「标准」段放 system 消息（输出契约在 user 消息，自然最后）；
    本地是单条消息，只能手工插位。
    """
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    llm = FakeLLM([_arcs_payload()])

    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    prompt = llm.calls[0]
    assert prompt.index(BLOCK_HEAD) < prompt.index("【输出格式】")


# ------------------------------------------------------------------ 读盘纪律

async def test_checklist_read_runs_in_worker_thread(project, monkeypatch):
    """清单**读盘**走 ``asyncio.to_thread``（P4 I-1：IO 密集活不在事件循环上）。

    LLM 调用**不**在 to_thread 里（这里只盯读盘）。
    """
    import asyncio

    from harness.tools import outline as outline_mod
    from harness.tools.outline import plan_arcs

    info, store, _user = project
    state = {"in_thread": False, "read_in_thread": []}
    real_to_thread = asyncio.to_thread
    real_block = outline_mod.stage_checklist_block

    async def spy_to_thread(func, /, *args, **kwargs):
        state["in_thread"] = True
        try:
            return await real_to_thread(func, *args, **kwargs)
        finally:
            state["in_thread"] = False

    def spy_block(root, stage, **kw):
        state["read_in_thread"].append(state["in_thread"])
        return real_block(root, stage, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(outline_mod, "stage_checklist_block", spy_block)

    llm = FakeLLM([_arcs_payload()])
    await plan_arcs(store, llm, PromptLoader(home_dir=info.path), _settings(),
                    {"instruction": "规划一卷骨架"})

    assert state["read_in_thread"] == [True]

