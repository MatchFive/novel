"""P11 范围接线（规划链消费 brief 里的范围与前提）。

口径钉死：
- ``plan_arcs = N``（正整数）→ **逐卷情节只规划前 N 卷**（按**卷号**排序，不是文件顺序），
  范围外的卷**不调 LLM、不写盘**，观测**如实报跳过了几卷**；
- ``args.vol`` 显式指定某卷 → **以它为准**（明确指定 > brief 的范围）；
- ``plan_arcs = 0``（=全书）→ **不限卷**；
- 范围块**追加在最终 instruction 之后**——agent 的 ``args.instruction`` 会**整体替换**
  instruction 变量 → 范围是**约束**不是建议，不能被覆盖掉；
- 没有 state.json → 行为与 P10 前**完全一致**。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools.outline import plan_arcs
from harness.tools.plots import master_plan


def make_settings(pro="pro-model"):
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro=pro)},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "范围书")
    s = ProjectStore(info.path)
    s.upsert_arcs([
        {"vol": 1, "title": "卷甲", "goal": "g1", "summary": "s1",
         "conflict": "c1", "resolution": "r1"},
        {"vol": 2, "title": "卷乙", "goal": "g2", "summary": "s2",
         "conflict": "c2", "resolution": "r2"},
        {"vol": 3, "title": "卷丙", "goal": "g3", "summary": "s3",
         "conflict": "c3", "resolution": "r3"},
    ])
    return s


@pytest.fixture
def make_llm():
    """记录每次请求的提示词；按提示词里的【本卷】标题路由响应。"""
    def factory():
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            prompt = body["messages"][0]["content"]
            title = "卷丙"
            for t in ("卷甲", "卷乙", "卷丙"):
                if f"【本卷】{t}" in prompt:
                    title = t
                    break
            content = json.dumps({"plots": [{"title": f"{title}的情节", "summary": "概要",
                                             "conflict": "冲突", "resolution": "收束",
                                             "beats": ["起"]}],
                                  "characters": [], "need_web": []}, ensure_ascii=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": content},
                                                            "finish_reason": "stop"}],
                                              "usage": {"prompt_tokens": 3,
                                                        "completion_tokens": 7,
                                                        "total_tokens": 10}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="https://api.test")
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


def _write_scope(store, **brief):
    """往 state.json 写一份带范围口径的 brief。"""
    state = RunState.new(brief.pop("raw", "写个仙侠"), ["outline"])
    state.brief.update(brief)
    state.save(store.root)


async def _run(store, llm, args=None):
    return await master_plan(store, llm, PromptLoader(home_dir=store.root),
                             make_settings(), lambda k, p: None, args or {})


# ------------------------------------------------------------------ 限卷

async def test_master_plan_limits_to_first_n_volumes(store, make_llm):
    """``plan_arcs = 1`` → **只规划第 1 卷**（第 2、3 卷没落库、没调 LLM）。"""
    _write_scope(store, plan_arcs=1)
    llm, calls = make_llm()

    result = await _run(store, llm)

    assert len(calls) == 1                      # 只调了一卷
    assert len(store.read_plots(1)) == 1
    assert store.read_plots(2) == []
    assert store.read_plots(3) == []
    assert "成功 1 卷" in result.text


async def test_master_plan_skips_other_volumes_observably(store, make_llm):
    """范围外的卷**如实报数**（不许静默少做）。"""
    _write_scope(store, plan_arcs=1)
    llm, _calls = make_llm()

    result = await _run(store, llm)

    assert "按范围跳过 2 卷" in result.text
    assert "前 1 卷" in result.text


async def test_master_plan_vol_arg_overrides_scope(store, make_llm):
    """``args.vol`` 显式指定某卷 → **以它为准**（明确指定 > brief 的范围）。"""
    _write_scope(store, plan_arcs=1)
    llm, calls = make_llm()

    result = await _run(store, llm, {"vol": 3})

    assert len(calls) == 1
    assert len(store.read_plots(3)) == 1
    assert store.read_plots(1) == []            # 第 1 卷**没有**被规划
    assert "成功 1 卷" in result.text


async def test_master_plan_scope_zero_means_all(store, make_llm):
    """``plan_arcs = 0``（=全书）→ **不限卷**。"""
    _write_scope(store, plan_arcs=0)
    llm, calls = make_llm()

    result = await _run(store, llm)

    assert len(calls) == 3
    assert "成功 3 卷" in result.text


async def test_master_plan_without_brief_is_unchanged(store, make_llm):
    """没有 state.json → **不限卷**、提示词里**没有**范围块（与 P10 前一致）。"""
    llm, calls = make_llm()

    result = await _run(store, llm)

    assert len(calls) == 3
    assert "成功 3 卷" in result.text
    assert "本次范围与前提" not in calls[0]["messages"][0]["content"]


async def test_master_plan_orders_by_volume_number(store, make_llm):
    """「前 N 卷」按**卷号**取，不是文件顺序。

    故意把 arcs.md 写成乱序（3、1、2）——若按文件顺序取前 1 卷，会拿到**第 3 卷**。
    """
    arcs_md = store.root / "outline" / "arcs.md"
    arcs_md.parent.mkdir(parents=True, exist_ok=True)
    arcs_md.write_text(
        "---\narcs:\n"
        "  - {vol: 3, title: 卷丙}\n"
        "  - {vol: 1, title: 卷甲}\n"
        "  - {vol: 2, title: 卷乙}\n"
        "---\n", encoding="utf-8")
    _write_scope(store, plan_arcs=1)
    llm, _calls = make_llm()

    await _run(store, llm)

    assert len(store.read_plots(1)) == 1        # 卷号最小的第 1 卷
    assert store.read_plots(3) == []            # **不是**文件顺序里的第一项
    assert len(store.read_plots(2)) == 0


# ------------------------------------------------------------------ instruction 注入

async def test_master_plan_scope_block_in_instruction(store, make_llm):
    """范围块进 ``{{instruction}}`` 变量（提示词模板一个字不动）。"""
    _write_scope(store, plan_arcs=1, goal="写一部长篇仙侠",
                 premise="少年修道成仙", constraints=["每章 3000 字"],
                 write_target="第一卷")
    llm, calls = make_llm()

    await _run(store, llm)

    prompt = calls[0]["messages"][0]["content"]
    assert "【本次范围与前提（来自需求解析）】" in prompt
    assert "目标：写一部长篇仙侠" in prompt
    assert "前提：少年修道成仙" in prompt
    assert "- 每章 3000 字" in prompt
    assert "范围：本次只规划前 1 卷的详细情节" in prompt
    assert "正文范围：第一卷" in prompt


async def test_master_plan_instruction_keeps_scope_after_agent_override(store, make_llm):
    """agent 传了 ``args.instruction`` → **范围块仍在**（范围是约束，不是建议）。"""
    _write_scope(store, plan_arcs=1, goal="写一部长篇仙侠")
    llm, calls = make_llm()

    await _run(store, llm, {"instruction": "只写仙侠，别写科幻"})

    prompt = calls[0]["messages"][0]["content"]
    assert "只写仙侠，别写科幻" in prompt
    assert "【本次范围与前提（来自需求解析）】" in prompt
    assert "目标：写一部长篇仙侠" in prompt


async def test_plan_arcs_instruction_has_scope_block(store, make_llm):
    """段一（卷骨架）的 instruction **也**带范围块（但**不限卷**）。"""
    _write_scope(store, plan_arcs=1, goal="写一部长篇仙侠",
                 constraints=["每章 3000 字"])
    _llm, calls = make_llm()

    arcs_payload = json.dumps({"planned_volume_count": 1, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r", "capacity_hint": "约40章"}]}, ensure_ascii=False)

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": arcs_payload},
                                                        "finish_reason": "stop"}],
                                          "usage": {"prompt_tokens": 3,
                                                    "completion_tokens": 7,
                                                    "total_tokens": 10}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.test")
    client = OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="m", pro="m"), http_client=http)

    await plan_arcs(store, client, PromptLoader(home_dir=store.root),
                    make_settings(), {"instruction": "规划一卷骨架"})

    prompt = calls[0]["messages"][0]["content"]
    assert "【本次范围与前提（来自需求解析）】" in prompt
    assert "目标：写一部长篇仙侠" in prompt
    assert "- 每章 3000 字" in prompt
    # **M1**：段一**不得**出现限卷行——模板写着「⑤ 严格遵守规划诉求里的每一条限制」，
    # 模型照办 → 全书卷骨架被压成 N 卷。
    assert "只规划前 1 卷" not in prompt


def test_prompt_templates_untouched_by_scope():
    """范围块走**已有的** ``{{instruction}}`` 变量 → **模板变量集不变**。

    （变量集被契约测试钉死：加变量会破坏 P2/P6 的契约测试。）
    """
    import re

    builtin = Path(r"d:/project/novel-harness/src/harness/prompts/builtin")
    for key, expected in (
            ("task.auto_master_plan", {"instruction"}),
            ("task.auto_plan_plots",
             {"instruction", "target_words", "total_chapters", "arc_chapters",
              "arc_title", "arc_goal", "arc_summary", "arc_conflict", "arc_resolution",
              "existing_characters", "existing_world", "scale_block", "research",
              "meme_bank"})):
        text = (builtin / f"{key}.md").read_text(encoding="utf-8")
        found = set(re.findall(r"\{\{(\w+)\}\}", text))
        assert found == expected, (key, sorted(found ^ expected))
