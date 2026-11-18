"""P2 验收三场景（LLM 全 mock：JSON-body MockTransport 按 prompt 内容锚点路由）。

1. 全规划链：brief → plan_arcs（卷骨架落盘）→ master_plan（两卷情节落 outline/plots/vol-N.md，
   seq 卷内 1..N、status planned）→ plan_chapters（plot_id "1-1"）：6 段×1500 字、README
   每章 3000 字 → 3 章（章数由内容量推出）；首章 meta beats/covers/plot_id 逐字；units 回写
   情节卡；events.log 钉 chapters_planned 三阶段（units/beats/selfcheck）+ plan_arc×2
   + planning_progress。
2. 幂等续拆：同 plot_id 再调 plan_chapters → 观测含「已拆…跳过」、chapters/ 文件数不变、
   工具内部 LLM 调用零新增（新调用全是步决策）。
3. 断点续跑：plan_chapters 首轮 units/headers 成功后 beats 全坏（章骨架落盘、无节拍）→
   run paused → 新 loop 走 P1 resume 路径（WorkMemory.rebuild from EventLog tail）→
   重入走「缺 beats 只补」分支：units/headers 零再调用（新调用只有 beats×3 + selfcheck×1），
   beats 落盘，run done。

MockTransport 路由锚点（按模板节标题）：【可用工具】→ 步决策脚本队列；【规划诉求】→ 卷骨架；
【本卷】{arc_title} → 两卷情节（标题区分卷）；【本次要拆的情节】→ units；【章节划分 → headers；
【本章所属情节】→ beats；【章节清单 → selfcheck。夹具与断言风格仿 tests/test_p1_acceptance.py。
"""
import asyncio
import json
import re
from pathlib import Path

import httpx

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools import (
    readonly_registry,
    register_chapter_tools,
    register_modify_tools,
    register_outline_tools,
    register_plot_tools,
)

BRIEF = "从零到卷骨架：一本双卷修仙小说，第一卷踏上修行路，第二卷统一北境"


# ---------------------------------------------------------------- 夹具与 mock 工厂

def _settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="standard-model", pro="pro-model")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def make_llm(router):
    """router(body) -> (content: str, usage: dict | None)；记录每次请求 body 供断言。

    形态抄 tests/tools/test_chapters.py 的 factory（JSON-body MockTransport）；
    OpenAICompatClient 非流式 complete 路径（P1 验收的 complete 级注入同款）。
    """
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        content, usage = router(body)
        payload = {"choices": [{"message": {"content": content},
                                "finish_reason": "stop"}]}
        if usage:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.test")
    client = OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="m", pro="m"),
        http_client=http)
    return client, calls


def _build_loop(store, llm, state, memory, events):
    settings = _settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    register_plot_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_chapter_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    return AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=list(state.brief["deliverables"]), settings=settings)


def _new_run(store, brief=BRIEF):
    state = RunState.new(brief, ["outline"])
    memory = WorkMemory()
    memory.record({"kind": "run_started", "payload": {"raw": brief}})
    return state, memory


def _decision(action, args, thought="推进规划链", say=None):
    return json.dumps({"thought": thought, "say": say, "action": action, "args": args},
                      ensure_ascii=False)


def _finish(summary="完成"):
    return _decision("finish", {"summary": summary}, thought="目标达成", say="收工")


# ---------------------------------------------------------------- 响应载荷

def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def _arcs_payload():
    return json.dumps({"planned_volume_count": 2, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "踏上修行路", "summary": "山村少年入宗门",
         "conflict": "资质被夺", "resolution": "逆天改命", "capacity_hint": "约30章"},
        {"vol": 2, "title": "征战", "goal": "统一北境", "summary": "宗门崛起战",
         "conflict": "北境诸部", "resolution": "会盟称雄", "capacity_hint": "约30章"},
    ]}, ensure_ascii=False), U()


def _plots_payload(*titles):
    """段二契约输出：plots + characters + need_web（多余键忽略不拒绝）。"""
    return json.dumps({"plots": [
        {"title": t, "summary": f"{t}之概要", "conflict": "冲突", "resolution": "收束",
         "beats": ["起", "承"]} for t in titles],
        "characters": [{"name": "酱油", "role_type": "support"}],
        "need_web": ["某现实事实"]}, ensure_ascii=False), U()


def _unit(seq, words=1500):
    return {"seq": seq, "time": f"第{seq}日", "location": "山道", "characters": ["甲"],
            "event": f"事件{seq}", "expected_words": words, "covers": f"覆盖{seq}"}


def _units_payload(units):
    return json.dumps({"units": units, "need_web": ["某现实事实"]}, ensure_ascii=False), U()


def _headers_payload(titles):
    return json.dumps({"chapters": [{"index": i, "title": t, "objective": o}
                                    for i, (t, o) in enumerate(titles, 1)],
                       "need_web": []}, ensure_ascii=False), U()


def _beats_payload(chapter_no, n=6):
    """6 拍 ≥ min_beats(3000)=5：一次过、不触发补足二轮。"""
    return json.dumps({
        "scene": f"第{chapter_no}章场景",
        "characters": [{"name": "甲", "role": "主角"}],
        "plot": [{"pov": "甲", "location": "山道", "event": f"第{chapter_no}章第{k}拍",
                  "weight": 2} for k in range(1, n + 1)],
        "dialogue_hooks": ["想保留的台词"], "humor_points": [], "ending_hook": "钩子悬着",
        "need_web": ["某现实事实"],
    }, ensure_ascii=False), U()


_SELFCHECK_OK = json.dumps({"ok": True, "issues": [], "fixes": [], "need_web": []},
                           ensure_ascii=False)


def make_router(decisions, *, beats_ok=True):
    """决策走脚本队列（先进先出）；工具调用按模板节标题锚点路由。

    beats_ok=False 时 beats 锚点返回坏 JSON（场景 C 构造「beats 阶段全败、骨架落盘」）。
    返回 (router, state)：state["beats_ok"] 可在中途翻回 True（模拟续跑后恢复）。
    """
    state = {"beats_ok": beats_ok}

    def router(body):
        prompt = body["messages"][0]["content"]
        if "【可用工具】" in prompt:                        # 步决策（auto_agent_step）
            return decisions.pop(0), U()
        if "【规划诉求】" in prompt:                        # plan_arcs（auto_master_plan）
            return _arcs_payload()
        if "【本卷】" in prompt:                            # master_plan（auto_plan_plots，按卷标题区分）
            if "【本卷】崛起" in prompt:
                return _plots_payload("入门", "扬名")
            if "【本卷】征战" in prompt:
                return _plots_payload("会盟", "北征")
            raise AssertionError(f"路由不到卷标题：{prompt[:80]}…")
        if "【本次要拆的情节】" in prompt:                  # plan_chapters 段一（plot_units）
            return _units_payload([_unit(i) for i in range(1, 7)])
        if "【章节划分" in prompt:                          # plan_chapters 段二（chapter_headers）
            return _headers_payload([("山道初行", "入宗闯关"), ("外门立足", "站稳脚跟"),
                                     ("内门选拔", "夺魁而归")])
        if "【本章所属情节】" in prompt:                    # plan_chapters 段三（chapter_beats）
            if not state["beats_ok"]:
                return "不是 JSON", U(1, 2)
            m = re.search(r"【本章】第(\d+)章", prompt)
            return _beats_payload(int(m.group(1)))
        if "【章节清单" in prompt:                          # plan_chapters 段四（chapter_selfcheck）
            return _SELFCHECK_OK, U()
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    return router, state


# ---------------------------------------------------------------- 场景 A：全规划链

def test_scenario_a_full_planning_chain(tmp_path):
    info = create_project(tmp_path, "规划链书",
                          story={"chapter_words": "3000", "target_chapters": "60",
                                 "planned_volumes": "2"})
    store = ProjectStore(info.path)
    decisions = [_decision("plan_arcs", {"instruction": BRIEF}, say="先落卷骨架"),
                 _decision("master_plan", {}, say="逐卷规划情节"),
                 _decision("plan_chapters", {"plot_id": "1-1"}, say="拆第一情节的章"),
                 _finish("规划链完成")]
    router, _ = make_router(decisions)
    llm, calls = make_llm(router)
    state, memory = _new_run(store)
    loop = _build_loop(store, llm, state, memory, EventLog(store.root))
    result = asyncio.run(loop.run())
    assert result.status == "done"

    # 卷骨架两卷落盘（含 goal）
    arcs = store.read_arcs()
    assert [a["vol"] for a in arcs] == [1, 2]
    assert all(a.get("goal") for a in arcs)
    arcs_md = (store.root / "outline" / "arcs.md").read_text(encoding="utf-8")
    assert "崛起" in arcs_md and "踏上修行路" in arcs_md

    # 两卷情节：seq 卷内 1..N 连续、status planned、标题无章号后缀、落 outline/plots/vol-N.md
    for vol, want in ((1, ["入门", "扬名"]), (2, ["会盟", "北征"])):
        plots = store.read_plots(vol)
        assert [p["title"] for p in plots] == want
        assert [p["seq"] for p in plots] == list(range(1, len(plots) + 1))
        assert all(p["status"] == "planned" for p in plots)
        assert not any(re.match(r"^第\d+章", p["title"]) for p in plots)
        assert (store.root / "outline" / "plots" / f"vol-{vol:02d}.md").exists()

    # 拆章：6 段×1500 字 ÷ README 每章 3000 字 → 3 章（章数由内容量推出）
    chapters = store.list_chapters()
    assert [c.seq for c in chapters] == [1, 2, 3]
    assert len(list((store.root / "chapters").iterdir())) == 3
    # 首章 meta 逐字段：plot_id/status/beats/covers 逐字
    first = chapters[0].meta
    assert first["plot_id"] == "1-1"
    assert first["status"] == "planned"
    assert first["title"] == "山道初行" and first["objective"] == "入宗闯关"
    assert first["covers"] == "覆盖1；覆盖2"                    # group_anchors 逐字
    assert first["beats"] == [{"pov": "甲", "location": "山道", "event": f"第1章第{k}拍",
                               "weight": 2} for k in range(1, 7)]
    assert first["target_words"] == 3000                       # README 每章字数落 meta
    assert chapters[1].meta["covers"] == "覆盖3；覆盖4"
    assert [b["event"] for b in chapters[2].meta["beats"]][:2] == ["第3章第1拍", "第3章第2拍"]

    # units 回写情节卡：compact JSON 单线 KV，且不冲概要等字段
    card = store.read_plots(1)[0]
    assert json.loads(card["units"])[1]["event"] == "事件2"
    assert card["title"] == "入门" and card["summary"] == "入门之概要"

    # events.log 钉 chapters_planned 三阶段 + plan_arc×2 + planning_progress
    evs = [json.loads(line) for line in
           (store.root / ".harness" / "events.log").read_text(encoding="utf-8").splitlines()]
    kinds = [e["kind"] for e in evs]
    ch_events = [e for e in evs if e["kind"] == "chapters_planned"]
    assert [e["payload"]["phase"] for e in ch_events] == ["units", "beats", "selfcheck"]
    assert ch_events[0]["payload"]["units"] == 6
    assert ch_events[1]["payload"]["ok"] == 3 and ch_events[1]["payload"]["missing"] == []
    plan_arcs = [e for e in evs if e["kind"] == "plan_arc"]
    assert [e["payload"]["vol"] for e in plan_arcs] == [1, 2]
    assert "planning_progress" in kinds and "agent_step" in kinds and "tool_result" in kinds
    # 调用序：决策4 + 卷骨架1 + 逐卷情节2 + units1 + headers1 + beats3 + selfcheck1 = 13
    assert len(calls) == 13


# ---------------------------------------------------------------- 场景 B：幂等续拆

def test_scenario_b_idempotent_replan_skips(tmp_path):
    info = create_project(tmp_path, "幂等书",
                          story={"chapter_words": "3000", "target_chapters": "60",
                                 "planned_volumes": "2"})
    store = ProjectStore(info.path)
    router, _ = make_router([_decision("plan_arcs", {"instruction": BRIEF}),
                             _decision("master_plan", {}),
                             _decision("plan_chapters", {"plot_id": "1-1"}),
                             _finish("规划链完成")])
    llm1, _calls1 = make_llm(router)
    state, memory = _new_run(store)
    assert asyncio.run(_build_loop(store, llm1, state, memory, EventLog(store.root)).run()) \
        .status == "done"
    n_files = len(list((store.root / "chapters").iterdir()))
    metas_before = [c.meta for c in store.list_chapters()]
    assert n_files == 3

    # 同 plot_id 再调 plan_chapters（新 run、新 client——loop 停机即关闭注入 client）
    router2, _ = make_router([_decision("plan_chapters", {"plot_id": "1-1"}),
                              _finish("幂等确认")])
    llm2, calls2 = make_llm(router2)
    state2, memory2 = _new_run(store)
    loop2 = _build_loop(store, llm2, state2, memory2, EventLog(store.root))
    assert asyncio.run(loop2.run()).status == "done"

    # 观测含「已拆…跳过」
    evs = EventLog(store.root).tail(50)
    obs = [e for e in evs if e["kind"] == "tool_result"
           and e["payload"]["tool"] == "plan_chapters"]
    assert obs and "已拆" in obs[-1]["payload"]["observation"]
    assert "跳过" in obs[-1]["payload"]["observation"]
    # chapters/ 文件数不变、meta 原样
    assert len(list((store.root / "chapters").iterdir())) == n_files
    assert [c.meta for c in store.list_chapters()] == metas_before
    # LLM 调用零新增：两次决策之外没有任何工具内部调用（units/headers/beats/selfcheck 全静默）
    assert len(calls2) == 2                                  # plan_chapters 决策 + finish 决策
    assert all("【可用工具】" in b["messages"][0]["content"] for b in calls2)


# ---------------------------------------------------------------- 场景 C：断点续跑（只补 beats）

def test_scenario_c_resume_backfills_beats_only(tmp_path):
    info = create_project(tmp_path, "断点书",
                          story={"chapter_words": "3000", "target_chapters": "60",
                                 "planned_volumes": "2"})
    store = ProjectStore(info.path)
    decisions = [_decision("plan_arcs", {"instruction": BRIEF}),
                 _decision("master_plan", {}),
                 _decision("plan_chapters", {"plot_id": "1-1"}),
                 _decision("pause", {}, thought="先暂停"),
                 _decision("plan_chapters", {"plot_id": "1-1"}),
                 _finish("断点续跑完成")]
    router, rstate = make_router(decisions, beats_ok=False)
    llm1, _calls1 = make_llm(router)
    events = EventLog(store.root)
    state = RunState.new(BRIEF, ["outline"])
    state.save(store.root)
    events.append("run_started", {"raw": BRIEF, "deliverables": ["outline"]})  # REPL 职责
    memory = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    loop1 = _build_loop(store, llm1, state, memory, events)
    asyncio.run(loop1.run())
    assert RunState.load(store.root).status == "paused"      # 拆章中途 beats 阶段全败 → paused

    # 骨架已落盘、全部无节拍（幂等门判「缺 beats」的依据）
    chapters = store.list_chapters()
    assert [c.seq for c in chapters] == [1, 2, 3]
    assert all(c.meta["beats"] == [] for c in chapters)
    assert len(list((store.root / "chapters").iterdir())) == 3

    # P1 resume 路径：回放 EventLog tail 重建工作记忆 + 新 AgentLoop
    memory2 = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    assert any(BRIEF in d for d in memory2.decided)          # decided 层含原 brief 原文
    rstate["beats_ok"] = True                                # 续跑后 beats 锚点恢复成功响应
    llm2, calls2 = make_llm(router)                          # loop1 已关闭注入 client：新 client 续跑
    loop2 = _build_loop(store, llm2, RunState.load(store.root), memory2, EventLog(store.root))
    result = asyncio.run(loop2.run())
    assert result.status == "done"

    # units/headers 阶段 LLM 零再调用：新调用只有 2 次步决策 + beats×3 + selfcheck×1
    prompts = [b["messages"][0]["content"] for b in calls2]
    assert len(calls2) == 6
    assert sum("【可用工具】" in p for p in prompts) == 2            # plan_chapters 决策 + finish 决策
    assert all("【本次要拆的情节】" not in p and "【章节划分" not in p for p in prompts)
    assert sum("【本章所属情节】" in p for p in prompts) == 3
    assert sum("【章节清单" in p for p in prompts) == 1
    # 补全后 beats 落盘；续拆只走 beats/selfcheck 两阶段（units 阶段事件不重发）
    chapters = store.list_chapters()
    assert all(c.meta["beats"] for c in chapters)
    assert [b["event"] for b in chapters[0].meta["beats"]][:2] == ["第1章第1拍", "第1章第2拍"]
    phases = [e["payload"]["phase"] for e in EventLog(store.root).tail(200)
              if e["kind"] == "chapters_planned"]
    # loop1（beats 全败 → 无 selfcheck 事件）+ loop2 续拆（beats/selfcheck）
    assert phases == ["units", "beats", "beats", "selfcheck"]
