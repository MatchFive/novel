"""独立审核（2026-11-15）点名的缺陷的**回归测试**。

逐条对应审核报告：
- M1：段一（卷骨架）的 instruction **不得**含限卷行（否则全书骨架被压成 N 卷）
- M2：``args.vol`` 压过范围 → 观测不说假话、提示词无互斥约束
- M3：骨架卷号重复 → 「前 N 卷」按**卷集合**取（不重复规划同一卷）
- M6：``brief.constraints`` 是字符串 → **当一条**限制（不逐字拆成单字）
- D1：``/fix`` 多章在**同一个事件循环**里跑（不跨 loop 复用 LLM 客户端）
- D2：章级意见**不**按「本轮未报即清」处理（模型输出不是逐章判定）→ 改为**可见**
- D3：``SKILL.md`` 带 BOM → frontmatter 仍能解析（不落成 ``skills/SKILL/``）
- M5：BM25 候选 id **全局唯一**（同章多条经历不共用 id → top-k 不被一章占满）
- M7：坏 ``arcs.md`` → 规划链工具**返回分类观测**，不抛栈
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools.outline import plan_arcs
from harness.tools.plots import master_plan


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test", default_tier="standard", web_verify=True)


def _write_scope(store, **brief):
    state = RunState.new(brief.pop("raw", "写个仙侠"), ["outline"])
    state.brief.update(brief)
    state.save(store.root)


def _llm(router):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        content = router(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.test")
    return OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="m", pro="m"), http_client=http), calls


def _plots_payload(title="卷甲"):
    return json.dumps({"plots": [{"title": f"{title}的情节", "summary": "概要",
                                  "conflict": "冲突", "resolution": "收束",
                                  "beats": ["起"]}],
                       "characters": [], "need_web": []}, ensure_ascii=False)


# ------------------------------------------------------------------ M1

async def test_m1_stage1_instruction_has_no_volume_limit(tmp_path):
    """**M1**：段一（卷骨架）的 instruction **不得**出现「只规划前 N 卷」。

    段一模板写着「⑤ 严格遵守规划诉求里的**每一条限制**」→ 模型照办 →
    **全书卷骨架被压成 N 卷**（审核实测）。brief 的 plan_arcs 是「规划哪几卷做详细情节」，
    不是「骨架只写 N 卷」。
    """
    info = create_project(tmp_path, "M1 书")
    store = ProjectStore(info.path)
    _write_scope(store, plan_arcs=1, goal="写一部长篇仙侠")
    llm, calls = _llm(lambda body: json.dumps(
        {"planned_volume_count": 1, "arcs": [
            {"vol": 1, "title": "崛起", "goal": "g", "summary": "s", "conflict": "c",
             "resolution": "r", "capacity_hint": "约40章"}]}, ensure_ascii=False))

    await plan_arcs(store, llm, PromptLoader(home_dir=store.root), make_settings(),
                    {"instruction": "规划一卷骨架"})

    prompt = calls[0]["messages"][0]["content"]
    assert "只规划前 1 卷" not in prompt
    assert "【本次范围与前提（来自需求解析）】" in prompt      # 其余段仍在


# ------------------------------------------------------------------ M2

async def test_m2_vol_arg_no_false_scope_in_observation(tmp_path):
    """**M2**：`args.vol` 压过范围 → 观测**不说假话**、提示词**无互斥约束**。

    观测说「本次范围：前 N 卷」但实际规划的是第 vol_arg 卷 → 说假话；
    提示词同时含「只为下面这一卷」与「只规划前 N 卷」→ 互斥约束。
    """
    info = create_project(tmp_path, "M2 书")
    store = ProjectStore(info.path)
    store.upsert_arcs([
        {"vol": 1, "title": "卷甲", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r"},
        {"vol": 2, "title": "卷乙", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r"},
        {"vol": 3, "title": "卷丙", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r"},
    ])
    _write_scope(store, plan_arcs=1, goal="写一部长篇仙侠")
    llm, calls = _llm(lambda body: _plots_payload())

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               make_settings(), lambda k, p: None, {"vol": 3})

    # 观测：说「按指定卷号」，**不说**「本次范围：前 1 卷」
    assert "本次范围：前 1 卷" not in result.text
    assert "按指定卷号规划第 3 卷" in result.text
    # 提示词：无互斥约束
    prompt = calls[0]["messages"][0]["content"]
    assert "只规划前 1 卷" not in prompt
    assert "目标：写一部长篇仙侠" in prompt       # 其余范围段仍在


# ------------------------------------------------------------------ M3

async def test_m3_duplicate_volumes_are_merged(tmp_path):
    """**M3**：骨架卷号重复 → 「前 N 卷」按**卷集合**取（同一卷不规划两次）。

    原实现按**条目**取前 N 条 → 同一卷被规划两次（后者覆盖前者），
    而观测报「成功 N 卷」（审核实测）。
    """
    info = create_project(tmp_path, "M3 书")
    store = ProjectStore(info.path)
    arcs_md = store.root / "outline" / "arcs.md"
    arcs_md.parent.mkdir(parents=True, exist_ok=True)
    arcs_md.write_text(
        "---\narcs:\n"
        "  - {vol: 1, title: 卷甲}\n"
        "  - {vol: 1, title: 卷甲重复}\n"
        "  - {vol: 2, title: 卷乙}\n"
        "---\n", encoding="utf-8")
    _write_scope(store, plan_arcs=2)
    llm, calls = _llm(lambda body: _plots_payload())

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               make_settings(), lambda k, p: None, {})

    # 两个**不同**的卷各规划一次（不是同一卷两次）
    assert len(calls) == 2
    assert len(store.read_plots(1)) == 1
    assert len(store.read_plots(2)) == 1
    assert "合并 1 条重复卷号" in result.text


# ------------------------------------------------------------------ M6

def test_m6_constraints_string_is_one_constraint(tmp_path):
    """**M6**：``brief.constraints`` 是**字符串** → 当**一条**限制（不逐字拆成单字）。

    「每章 3000 字」若被逐字拆开 → 7 行「- 每 / - 章 / …」（审核实测）。
    """
    from harness.run.scope import read_brief_scope

    info = create_project(tmp_path, "M6 书")
    store = ProjectStore(info.path)
    state = RunState.new("写个仙侠", ["outline"])
    state.brief["constraints"] = "每章 3000 字"
    state.save(store.root)

    scope = read_brief_scope(store)
    assert scope.constraints == ["每章 3000 字"]
    assert "- 每章 3000 字" in scope.block()
    assert "- 每\n" not in scope.block()


# ------------------------------------------------------------------ D1

def test_d1_fix_chapters_share_one_event_loop(tmp_path, monkeypatch):
    """**D1**：``/fix`` 的所有章在**同一个事件循环**里跑（``asyncio.run`` 只调一次）。

    逐章 ``asyncio.run`` → 第 2 章起复用**上一个已关闭的 loop** 的 httpx 连接池 →
    ``RuntimeError: Event loop is closed``（不是 LLMError → 不被捕获）→ 穿出 REPL
    命令分发（无兜底）→ **整个会话带 traceback 退出**（审核实测推演）。
    """
    import asyncio

    from harness import repl as repl_mod
    from harness.run.issues import IssuesStore
    from harness.store.project import ChapterDoc

    info = create_project(tmp_path, "D1 书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一章"},
                                   body="甲。夜里山门起火。乙握紧了剑。"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "第二章"},
                                   body="丙。天亮了。丁倒在门外。"))
    issues = IssuesStore(store.root)
    issues.add("check", "问题甲", "详情", "夜里山门起火", chapter=1)
    issues.add("check", "问题乙", "详情", "丁倒在门外", chapter=2)

    seen: list[int] = []
    runs = {"n": 0}

    async def fake_fix_chapter(**kw):
        seen.append(kw["chapter"])
        # 记录当前 loop：两章必须在**同一个** loop 里
        runs.setdefault("loops", set()).add(id(asyncio.get_running_loop()))
        return "ok"

    real_run = asyncio.run

    def spy_run(coro):
        runs["n"] += 1
        return real_run(coro)

    class _Sess:
        def components(self):
            from harness.session import Components
            return Components(object(), object(), object())

    sess = _Sess()
    sess.store = store
    sess.settings = make_settings()
    sess.events = None

    monkeypatch.setattr("harness.check.fix.fix_chapter", fake_fix_chapter)
    monkeypatch.setattr(repl_mod.asyncio, "run", spy_run)

    repl_mod._cmd_fix(sess)

    assert seen == [1, 2]                  # 两章都处理了
    assert runs["n"] == 1                  # **只开了一个事件循环**
    assert len(runs["loops"]) == 1          # 两章在同一个 loop 里


# ------------------------------------------------------------------ D3

def test_d3_skill_with_bom_is_parsed(tmp_path):
    """**D3**：带 **UTF-8 BOM** 的 ``SKILL.md`` → frontmatter **仍能解析**。

    否则：BOM 让 frontmatter 判为不存在 → 名字回落成 ``src.stem`` = "SKILL" →
    skill 落成 ``skills/SKILL/SKILL.md``（审核实测）。
    """
    from harness.skills import scan_skills

    skills_dir = tmp_path / ".harness" / "skills" / "我的技能"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_bytes(
        b"\xef\xbb\xbf" + "---\nname: 我的技能\ndescription: 带 BOM 的技能\n---\n\n正文\n"
        .encode())

    found = scan_skills(tmp_path)
    assert "我的技能" in found
    assert found["我的技能"].description == "带 BOM 的技能"


# ------------------------------------------------------------------ M5

def test_m5_bm25_candidate_ids_are_unique(tmp_path):
    """**M5**：BM25 候选 id **全局唯一**（同章多条经历不共用 id）。

    原实现用章下标 → 同一章的 10 条经历共用一个 id → BM25 的 ``{id: score}`` 里
    后者覆盖前者、rrf 还累加 10 次 → top-8 全被一章占满（审核实测）。
    """
    from harness.memory import inject as inject_mod

    ids: list[int] = []
    orig_cand = inject_mod._Cand

    def spy_cand(id_, seq, payload, text):
        ids.append(id_)
        return orig_cand(id_, seq, payload, text)

    inject_mod._Cand = spy_cand
    try:
        inject_mod.build_memory_bundle(
            mem=_fake_mem(), index=None, pov="甲", cast_names=["乙"],
            cur_seq=9, query="山门 血战")
    finally:
        inject_mod._Cand = orig_cand

    # 无论上面是否跑通，都要求**候选 id 不重复**（同一批里）
    assert len(ids) == len(set(ids)), ids


def _fake_mem():
    """一章里有**多条**同角色经历（正是撞 id 的场景）。"""
    from harness.memory.store import MemoryStore

    class _Mem(MemoryStore):
        """只提供 ``_gather`` 用到的那两个接口。"""

        def __init__(self):
            self.root = Path("d:/tmp/_m5_mem")
            self._data = {
                5: {
                    "character_events": [
                        {"character": "甲", "summary": f"第{i}次经历：山门血战"}
                        for i in range(10)
                    ],
                },
            }

        def list_chapter_memories(self):
            return list(self._data)

        def read_chapter_memory(self, seq):
            return self._data.get(seq)

    return _Mem()


# ------------------------------------------------------------------ M7

async def test_m7_broken_arcs_file_returns_observation_not_raise(tmp_path):
    """**M7**：坏 ``arcs.md``（front-matter 坏 YAML）→ 规划链工具**返回分类观测**，
    **不抛栈**（违反「失败不 raise」）。审核实测：两个工具都抛 ``FileFormatError``。
    """
    info = create_project(tmp_path, "M7 书")
    store = ProjectStore(info.path)
    arcs_md = store.root / "outline" / "arcs.md"
    arcs_md.parent.mkdir(parents=True, exist_ok=True)
    arcs_md.write_text("---\narcs: [1, 2\n---\n", encoding="utf-8")   # 坏 YAML

    llm, _calls = _llm(lambda body: _plots_payload())

    result = await master_plan(store, llm, PromptLoader(home_dir=store.root),
                               make_settings(), lambda k, p: None, {})

    assert "失败" in result.text
    assert "文件损坏" in result.text
