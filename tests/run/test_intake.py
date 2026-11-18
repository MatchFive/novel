"""P10 需求解析：纯函数解析与字段裁决 + 执行链。

口径钉死：
- **提示词逐字移植** seed（底本 2590 字节，正文一字不改）；
- 模型 JSON → brief 字段的映射与**脏值兜底**（不抛异常）；
- **deliverables 是权限面**：过**本地**词表、用户显式优先、采纳模型值不可能扩大权限面；
- **plan_arcs** 用 seed 口径（任意非负整数，不限死 {0,1,3,5}）；
- intake **失败不阻断**：返回安全最小字典 + 如实观测；
- 观测**不带模型原文**。
"""
from __future__ import annotations

import json

from harness.prompts.loader import PromptLoader
from harness.run.intake import (
    DELIVERABLE_VOCAB,
    parse_intake,
    project_state_text,
    run_intake,
)
from harness.run.whitelist import DELIVERABLE_TOOLS, allowed_for_run
from harness.writing.pure import WRITING_ROUTING


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """顺序吐脚本的假 llm（记录每次提示词）。

    **脚本用完时重复最后一条**——``complete_structured`` 在解析失败时会重试一次，
    桩若直接抛 IndexError 就测不到「重试后仍失败」这条路径了。
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []
        self.provider = _Provider()

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


class _Provider:
    base_url = "https://api.test"
    api_key = "k"
    lite = "m-lite"
    standard = "m-std"
    pro = "m-pro"


# ------------------------------------------------------------------ 纯函数

def test_parse_intake_maps_fields():
    """正常 JSON → 各字段照落。"""
    data = {
        "goal": "写一部仙侠长篇",
        "premise": "少年修道成仙的故事",
        "deliverables": ["outline", "draft"],
        "constraints": ["每章 3000 字", "不写系统流"],
        "need_dimensions": ["力量体系"],
        "prefer": ["爽"],
        "avoid": ["系统流"],
        "plan_arcs": 3,
        "write_target": "第一卷",
    }
    got = parse_intake(data, raw="原始需求", deliverables=["outline"])
    assert got["goal"] == "写一部仙侠长篇"
    assert got["premise"] == "少年修道成仙的故事"
    assert got["constraints"] == ["每章 3000 字", "不写系统流"]
    # 权限面 = 用户的（模型推断另记为信息）
    assert got["deliverables"] == ["outline"]
    assert got["suggested_deliverables"] == ["outline", "draft"]
    assert got["plan_arcs"] == 3
    assert got["write_target"] == "第一卷"


def test_parse_intake_bad_json_returns_safe_minimum():
    """非法输入（不是 dict / None / 列表）→ **不抛**，返回安全最小字典。"""
    for bad in (None, [], "文本", 42, {"goal": 123}):
        got = parse_intake(bad, raw="原始需求", deliverables=["outline"])
        assert got["premise"] == "原始需求"      # 回落 raw
        assert got["deliverables"] == ["outline"]
        assert got["constraints"] == []
        assert got["goal"] == ""


def test_parse_intake_drops_invalid_constraints():
    """限制只收非空 str；超长截断；超量截断。"""
    got = parse_intake({"constraints": ["好的", "", "  ", 123, "x" * 500]},
                       raw="r", deliverables=["outline"])
    assert got["constraints"] == ["好的", "x" * 200]


def test_parse_intake_user_deliverables_win():
    """**用户显式指定**赢过模型推断（模型不能覆盖用户的显式声明）。"""
    got = parse_intake({"deliverables": ["outline"]}, raw="r",
                       deliverables=["draft"])
    assert got["deliverables"] == ["draft"]


def test_parse_intake_filters_unsupported_deliverables():
    """seed 模板里的 ``edit`` 本地**没有** → 过滤掉，**不抛**；``characters``
    **已支持**（P22/A6：D2 的 draft_character 落地后补进白名单）→ 保留。

    ``edit`` 透传进 ``allowed_for_run`` 会 ValueError 响亮失败（fail-closed）——本地必须拦。
    """
    got = parse_intake({"deliverables": ["characters", "edit", "draft"]},
                       raw="r", deliverables=["outline"])
    # 模型推断只作**信息**（suggested）记下：characters 保留（本地支持），edit 被过滤
    assert got["suggested_deliverables"] == ["characters", "draft"]
    assert "edit" not in got["suggested_deliverables"]
    # 权限面**不受模型影响**（D2）→ 仍是用户的值
    assert got["deliverables"] == ["outline"]
    allowed_for_run(got["deliverables"])


def test_parse_intake_model_never_changes_permission_set():
    """**D2**：模型的 deliverables 推断**永远不改权限面**（只作信息记下）。

    本地 ``deliverables`` 就是 ``allowed_for_run`` 的权限面：
    ``allowed_for_run(["outline"])`` = 24 个工具（全集），
    ``["world"]`` = 21（**没有规划链三工具**），
    ``["discuss"]`` = 11（**只读+问**）。
    若采纳模型的收窄 → 用户要「写个海洋救赎的故事」，run 里**物理上无法**产出卷骨架，
    循环多半在 ``MAX_NOOP_STREAK`` 后 paused，甚至「只补了几条世界观就 finish」——
    **run 标成成功但故事没写**。模型无权收窄用户的权限。
    """
    baseline = allowed_for_run(["outline"])
    for value in (["draft"], ["chapters"], ["world"], ["discuss"],
                  ["world", "draft"], ["outline"], ["characters", "edit"]):
        got = parse_intake({"deliverables": value}, raw="r",
                           deliverables=["outline"])
        assert got["deliverables"] == ["outline"], value
        assert allowed_for_run(got["deliverables"]) == baseline, value
        # 推断另记为信息（供 P11 用），不参与权限面
        assert "deliverables" not in got or got.get("suggested_deliverables") is not None


def test_parse_intake_user_explicit_deliverables_kept():
    """用户**显式指定**的交付物原样保留（模型无权改动）。"""
    got = parse_intake({"deliverables": ["outline", "draft"]},
                       raw="r", deliverables=["draft"])
    assert got["deliverables"] == ["draft"]


def test_parse_intake_plan_arcs_accepts_any_int():
    """``plan_arcs`` 用 seed 口径（任意非负整数）——限死 {0,1,3,5} 会反转语义。

    模型回 2（「先写两卷」）若被打成 0 = 「按全书推进」，比原样接受更糟。
    """
    assert parse_intake({"plan_arcs": 2}, raw="r", deliverables=["outline"])["plan_arcs"] == 2
    assert parse_intake({"plan_arcs": -5}, raw="r", deliverables=["outline"])["plan_arcs"] == 0
    assert parse_intake({"plan_arcs": "abc"}, raw="r", deliverables=["outline"])["plan_arcs"] == 0
    assert parse_intake({"plan_arcs": None}, raw="r", deliverables=["outline"])["plan_arcs"] == 0


def test_parse_intake_write_target_fallback():
    """write_target 缺失/非字符串 → 空串（=按规划一路推进）。"""
    assert parse_intake({}, raw="r", deliverables=["outline"])["write_target"] == ""
    assert parse_intake({"write_target": 123}, raw="r", deliverables=["outline"])["write_target"] == ""


def test_parse_intake_constraints_count_capped():
    """**B6**：限制条数上限 20（超出的丢弃）。"""
    got = parse_intake({"constraints": [f"限制{i}" for i in range(50)]},
                       raw="r", deliverables=["outline"])
    assert len(got["constraints"]) == 20
    assert got["constraints"][0] == "限制0"


# ------------------------------------------------------------------ 现状摘要（D3/D4）

def test_project_state_text_counts_world_entries_with_empty_readme(tmp_path):
    """**D3**：README 六字段**全空**（新建项目的默认模板就是全空）时，
    仍要报出世界观/角色计数——不能谎报「新项目：无既有内容」。

    错判命中的正是**主路径**（新建项目）：``{{project_state}}`` 是 intake 判断
    「继续做 vs 新开」的唯一依据 → 谎报成新项目 → 从零重规划、与既有设定打架。
    """
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.upsert_world_entries([{"title": "潮汐历法", "content": "每潮汐月改历",
                                  "category": "设定", "summary": "历法"}])

    out = project_state_text(store)
    assert "世界观条目 1 条" in out
    assert "新项目：无既有内容" not in out


def test_project_state_text_survives_broken_chapter(tmp_path):
    """**D4**：一个**坏章**（front-matter 坏 YAML）→ 只少算一章，
    **不**把整份现状吞成空串（否则 intake 对项目状态完全失明）。"""
    from harness.store.project import ChapterDoc, ProjectStore

    store = ProjectStore(tmp_path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一章"},
                                   body="正文"))
    bad = store.root / "chapters" / "0002-bad.md"
    bad.write_text("---\nseq: [1, 2\n---\n\n坏章\n", encoding="utf-8")

    out = project_state_text(store)      # 不抛
    assert "世界观条目" in out            # 现状没被吞空
    assert "章正文" in out


def test_story_settings_is_public():
    """**S1**：`story_settings` 是**公开名**（`run.intake` 跨模块引私有名不合适）。

    私有名一旦改名 → ImportError → 被 run_intake 吞掉 → 现状静默变空（D4 同款降级）。
    """
    from harness.planning import context

    assert hasattr(context, "story_settings")
    assert context.story_settings is context._story_settings


def test_deliverable_vocab_matches_local_whitelist():
    """本地封闭词表 = ``DELIVERABLE_TOOLS`` 的键（**同一口径不留两份**）。"""
    assert DELIVERABLE_VOCAB == frozenset(DELIVERABLE_TOOLS.keys())


# ------------------------------------------------------------------ 项目现状

def test_project_state_text_new_project(tmp_path):
    """空项目 → 「（新项目：无既有内容）」。"""
    from harness.store.project import ProjectStore

    out = project_state_text(ProjectStore(tmp_path))
    assert "新项目" in out


def test_project_state_text_lists_counts(tmp_path):
    """有内容的项目 → 报卷/章/世界观/角色数，并问「继续做还是新开」。"""
    from harness.store.project import ChapterDoc, ProjectStore

    store = ProjectStore(tmp_path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一章"},
                                   body="正文"))
    out = project_state_text(store)
    assert "1 章正文" in out
    assert "继续做" in out


# ------------------------------------------------------------------ 执行链

def _store(tmp_path):
    """真 store（project_state_text 要读卷/章/世界观/角色）。"""
    from harness.store.project import ProjectStore

    return ProjectStore(tmp_path)


def _prompts(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(home_dir=home)


async def test_run_intake_fills_brief(tmp_path):
    """正常路径 → 返回结构化字段 + 如实观测。"""
    llm = FakeLLM([json.dumps({
        "goal": "写一部仙侠长篇", "premise": "少年修道",
        "deliverables": ["outline"], "constraints": ["不写系统流"],
        "plan_arcs": 0, "write_target": ""}, ensure_ascii=False)])

    parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                    prompts=_prompts(tmp_path), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    assert parsed["goal"] == "写一部仙侠长篇"
    assert parsed["constraints"] == ["不写系统流"]
    assert "限制 1 条" in note
    # 提示词里带上了需求原文与项目现状
    assert "写个仙侠" in llm.calls[0]
    assert "{{message}}" not in llm.calls[0]


async def test_run_intake_llm_failure_does_not_block(tmp_path):
    """LLM 失败 → **不阻断**：返回安全最小字典 + 如实观测。"""
    llm = FakeLLM([Exception("boom")])

    parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                    prompts=_prompts(tmp_path), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    # 失败 → **None**（调用方据此不碰 brief：台账保持「只有 raw/deliverables」）
    assert parsed is None
    assert note.startswith("需求解析失败")
    assert "按原始需求继续" in note


async def test_run_intake_bad_json_does_not_raise(tmp_path):
    """模型回非 JSON → 不 raise，按失败处理。"""
    llm = FakeLLM(["这不是 JSON"])

    parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                    prompts=_prompts(tmp_path), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    assert parsed is None
    assert note.startswith("需求解析失败")


async def test_run_intake_render_failure_does_not_block(tmp_path):
    """**渲染失败**（模板缺失等）→ 与 LLM 失败同口径：不返回 brief、如实报因。

    变异检验 M4 暴露的盲区：此前只测了「LLM 失败」，这条分支没人管。
    """
    class _BadPrompts:
        def render(self, key, **vars):
            raise KeyError(key)

    llm = FakeLLM([json.dumps({"goal": "g", "premise": "p",
                                "deliverables": ["outline"], "constraints": [],
                                "plan_arcs": 0, "write_target": ""},
                               ensure_ascii=False)])

    parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                    prompts=_BadPrompts(), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    assert parsed is None
    assert note.startswith("需求解析失败")
    assert "按原始需求继续" in note
    assert llm.calls == []          # 渲染失败就不该调 LLM


async def test_run_intake_provider_failure_does_not_raise(tmp_path):
    """**S3**：provider / 路由取值抛异常 → **不得**穿出 ``run_intake``。

    穿出去的后果：异常一路到 REPL 的 ``_drive()`` → 而 REPL 主循环对普通命令
    **没有兜底 except** → **整个会话死掉**。
    """
    class _BadLLM:
        @property
        def provider(self):
            raise RuntimeError("provider 配置坏了")

    parsed, note = await run_intake(store=_store(tmp_path), llm=_BadLLM(),
                                    prompts=_prompts(tmp_path), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    assert parsed is None
    assert note.startswith("需求解析失败")


def test_project_state_text_has_all_six_readme_fields(tmp_path):
    """**S8**：README **六字段**都进现状摘要（计划裁定 5）。

    只取三项（题材/基调/结局）会漏掉**幅度级**信息（每章字数/总章数/卷数）——
    那正是 intake 判断「要写多长」的依据。
    """
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    (store.root / "README.md").write_text(
        "题材：仙侠\n基调：冷峻\n结局：开放\n每章字数：3000\n"
        "目标总章数：100\n预计卷数：5\n", encoding="utf-8")

    out = project_state_text(store)
    for label in ("题材：仙侠", "基调：冷峻", "结局：开放",
                  "每章字数：3000", "目标总章数：100", "预计卷数：5"):
        assert label in out, label


async def test_run_intake_observation_has_no_model_text(tmp_path):
    """观测**不带模型原文**（P6 裁定 54）：只带计数与派生信息。"""
    llm = FakeLLM([json.dumps({
        "goal": "写一部仙侠长篇", "premise": "少年修道成仙",
        "deliverables": ["outline"], "constraints": ["不写系统流"],
        "plan_arcs": 0, "write_target": ""}, ensure_ascii=False)])

    _parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                     prompts=_prompts(tmp_path), settings=None,
                                     raw="写个仙侠", deliverables=["outline"])

    assert "写一部仙侠长篇" not in note
    assert "少年修道成仙" not in note
    assert "不写系统流" not in note


async def test_run_intake_observation_says_goal_not_recorded_when_missing(tmp_path):
    """**S5**：模型没给目标 → 观测**不得**说「目标已记」（那是在说假话）。"""
    llm = FakeLLM([json.dumps({
        "goal": "", "premise": "少年修道成仙", "deliverables": ["outline"],
        "constraints": [], "plan_arcs": 0, "write_target": ""}, ensure_ascii=False)])

    _parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                     prompts=_prompts(tmp_path), settings=None,
                                     raw="写个仙侠", deliverables=["outline"])

    assert "目标已记" not in note
    assert "未给出目标" in note


async def test_run_intake_observation_reports_scope_honestly(tmp_path):
    """**M4**：观测**如实**报范围现状——``plan_arcs`` **已经**接到规划链（P11），
    再说「暂未生效」是**假话**；``write_target`` **只注入不拦** → 那部分仍要说明。"""
    llm = FakeLLM([json.dumps({
        "goal": "g", "premise": "p", "deliverables": ["outline"],
        "constraints": [], "plan_arcs": 2, "write_target": "第一卷"}, ensure_ascii=False)])

    _parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                     prompts=_prompts(tmp_path), settings=None,
                                     raw="写个仙侠", deliverables=["outline"])

    assert "暂未生效" not in note
    assert "本次范围：只规划前 2 卷的详细情节" in note


async def test_run_intake_observation_reports_model_suggestion(tmp_path):
    """模型推断的交付物与用户不同 → 观测**如实**报出来（只作参考，不改权限面）。"""
    llm = FakeLLM([json.dumps({
        "goal": "g", "premise": "p", "deliverables": ["world"],
        "constraints": [], "plan_arcs": 0, "write_target": ""}, ensure_ascii=False)])

    parsed, note = await run_intake(store=_store(tmp_path), llm=llm,
                                    prompts=_prompts(tmp_path), settings=None,
                                    raw="写个仙侠", deliverables=["outline"])

    assert parsed["deliverables"] == ["outline"]      # 权限面没变
    assert parsed["suggested_deliverables"] == ["world"]
    assert "world" in note
    assert "仅作参考" in note


async def test_run_intake_reads_project_state_in_thread(tmp_path, monkeypatch):
    """读项目现状走 ``asyncio.to_thread``（P4 I-1：IO 密集活不在事件循环上）。"""
    import asyncio

    import harness.run.intake as intake_mod

    state = {"in_thread": False, "read_in_thread": []}
    real_to_thread = asyncio.to_thread
    real_state = intake_mod.project_state_text

    async def spy_to_thread(func, /, *args, **kwargs):
        state["in_thread"] = True
        try:
            return await real_to_thread(func, *args, **kwargs)
        finally:
            state["in_thread"] = False

    def spy_state(store):
        state["read_in_thread"].append(state["in_thread"])
        return real_state(store)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(intake_mod, "project_state_text", spy_state)

    llm = FakeLLM([json.dumps({"goal": "g", "premise": "p",
                                "deliverables": ["outline"],
                                "constraints": [], "plan_arcs": 0,
                                "write_target": ""}, ensure_ascii=False)])
    await run_intake(store=_store(tmp_path), llm=llm, prompts=_prompts(tmp_path),
                     settings=None, raw="写个仙侠", deliverables=["outline"])

    assert state["read_in_thread"] == [True]


# ------------------------------------------------------------------ 路由

def test_intake_routing_pinned():
    """路由钉死（standard/0.3/2048，seed router.py 同值）。"""
    assert WRITING_ROUTING["task.task_intake"] == ("standard", 0.3, 2048)
