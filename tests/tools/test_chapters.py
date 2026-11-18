"""plan_chapters 拆章三步+自查（units→归组→headers→逐章 beats→selfcheck）/ 幂等续拆 / 退化派生 /
保拍数修复 / MockTransport 按 prompt 内容路由 / Semaphore(3) 钉死 / vol-seq 解析 / need_web 忽略。

golden 章数由内容量推出：4 units×800 字、target 4000 → 1 章；8 units×1500、target 3000 → 4 章。
mock 路由锚点取五模板节标题：【本次要拆的情节】/【章节划分（分组已定】/【本章所属情节】/
【章节清单（按序】——补足二轮附注（【剧情描述太少·补足】/【节拍太少·补足】）在 prompt 内可辨。
"""
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.planning.pure import DEFAULT_TARGET_WORDS, slim_unit
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.chapters import plan_chapters, register_chapter_tools
from harness.tools.registry import ToolRegistry


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


PLOT_1 = {"seq": 1, "title": "开端", "summary": "概要一", "conflict": "冲突一",
          "resolution": "收束一", "beats": ["起", "承"], "status": "planned"}
PLOT_2 = {"seq": 2, "title": "发展", "summary": "概要二", "conflict": "冲突二",
          "resolution": "收束二", "beats": ["起", "转"], "status": "planned"}


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g1", "summary": "s1",
                    "conflict": "c1", "resolution": "r1"}])
    s.upsert_plots(1, [dict(PLOT_1), dict(PLOT_2)])
    return s


@pytest.fixture
def make_llm():
    """router(body) -> (content: str, usage: dict | None)；记录每次请求 body 供断言。"""
    def factory(router):
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

    return factory


class SpyLLM:
    """complete 外包一层：统计最大并发 active；sleep(0) 强制挂起点（模拟真实网络延迟）。"""

    def __init__(self, inner):
        self._inner = inner
        self.active = 0
        self.max_active = 0

    async def complete(self, messages, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            return await self._inner.complete(messages, **kwargs)
        finally:
            self.active -= 1


class Collector:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))


def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def unit(seq, words, covers, event, time="清晨", loc="山脚"):
    return {"seq": seq, "time": time, "location": loc, "characters": ["甲"],
            "event": event, "expected_words": words, "covers": covers}


def units_payload(units, usage=(10, 20)):
    """units 输出契约（need_web 键必须被忽略、不得拒绝）。"""
    return json.dumps({"units": units, "need_web": ["某现实事实"]}, ensure_ascii=False), U(*usage)


def headers_payload(chapters, usage=(10, 20)):
    return json.dumps({"chapters": [{"index": i, "title": t, "objective": o}
                                    for i, t, o in chapters],
                       "need_web": []}, ensure_ascii=False), U(*usage)


def beats_payload(n, chapter_no, usage=(10, 20)):
    return json.dumps({
        "scene": f"第{chapter_no}章场景",
        "characters": [{"name": "甲", "role": "主角"}],
        "plot": [{"pov": "甲", "location": "山道", "event": f"第{chapter_no}章第{k + 1}拍",
                  "weight": 2} for k in range(n)],
        "dialogue_hooks": ["想保留的台词"],
        "humor_points": [],
        "ending_hook": "钩子悬着",
        "need_web": ["某现实事实"],
    }, ensure_ascii=False), U(*usage)


SELFCHECK_OK = json.dumps({"ok": True, "issues": [], "fixes": [], "need_web": []},
                          ensure_ascii=False)


def make_router(units_fn, headers_fn, beats_fn=None, selfcheck_fn=None):
    """按模板节标题路由五段 prompt；beats/selfcheck 缺省给成功响应。"""
    def beats_default(prompt):
        m = re.search(r"【本章】第(\d+)章", prompt)
        return beats_payload(5, int(m.group(1)))

    def selfcheck_default(prompt):
        return SELFCHECK_OK, U(10, 20)

    table = {
        "【本次要拆的情节】": units_fn,
        "【章节划分（分组已定": headers_fn,
        "【本章所属情节】": beats_fn or beats_default,
        "【章节清单（按序": selfcheck_fn or selfcheck_default,
    }

    def router(body):
        prompt = body["messages"][0]["content"]
        for anchor, fn in table.items():
            if anchor in prompt:
                return fn(prompt)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    return router


async def run_tool(store, llm, emit, args=None, settings=None):
    return await plan_chapters(store, llm, PromptLoader(home_dir=store.root),
                               settings or make_settings(), emit.emit, args or {})


UNITS_4 = [unit(i, 800, f"覆盖{i}", f"事件{i}") for i in range(1, 5)]
# 4 段×1500 字、目标 3000 → 2 章（selfcheck fixes 逐字段断言用两章布景）
UNITS_4W = [unit(i, 1500, f"覆盖{i}", f"事件{i}", time=f"第{i}日", loc="山道")
            for i in range(1, 5)]


async def test_golden_4_units_800_target_4000_one_chapter(store, make_llm):
    """golden：4 段×800 字、目标 4000 → 1 章；段数<min_units 触发补足二轮（变少丢弃）；
    beats<min_beats 触发补足二轮（变少丢弃）→ 保首轮 4 拍；写盘 meta 逐字段回放。"""
    def units_fn(prompt):
        if "【剧情描述太少·补足】" in prompt:
            return units_payload(UNITS_4[:3])        # 变少 → 丢弃，仍用首轮 4 段
        return units_payload(UNITS_4)

    def headers_fn(prompt):
        assert "第1章（含 4 段，预估 3200 字）" in prompt
        return headers_payload([(1, "独章题名", "独章目标")])

    def beats_fn(prompt):
        if "【节拍太少·补足】" in prompt:
            return beats_payload(3, 1)               # 变少 → 丢弃，仍用首轮 4 拍
        return beats_payload(4, 1)

    llm, calls = make_llm(make_router(units_fn, headers_fn, beats_fn))
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 4000})
    assert "拆章完成" in result.text and "归组 1 章" in result.text
    assert "目标 4000 字/章" in result.text
    # units 首轮+补足二轮 / headers 1 / beats 首轮+补足二轮 / selfcheck 1 = 6 次调用
    assert len(calls) == 6
    assert result.usage == CompletionUsage(prompt_tokens=60, completion_tokens=120)

    ch = store.read_chapter(1)
    m = ch.meta
    assert m["seq"] == 1
    assert m["title"] == "独章题名"
    assert m["objective"] == "独章目标"
    assert m["status"] == "planned"
    assert m["plot_id"] == "1-2"
    assert m["target_words"] == 4000
    assert m["covers"] == "；".join(u["covers"] for u in UNITS_4)
    assert m["time_span"] == "清晨"
    assert m["start_at"] == "山脚｜清晨"
    assert m["end_at"] == "山脚｜清晨"
    assert m["units"] == [slim_unit(u) for u in UNITS_4]
    assert m["beats"] == [{"pov": "甲", "location": "山道", "event": f"第1章第{k}拍",
                           "weight": 2} for k in range(1, 5)]
    assert ch.body == ""

    # units 回写情节卡：slim units 的 compact JSON 单线 KV，且情节其它字段不被冲掉
    plots = store.read_plots(1)
    assert plots[1]["units"] == json.dumps([slim_unit(u) for u in UNITS_4], ensure_ascii=False)
    assert plots[1]["summary"] == "概要二" and plots[1]["title"] == "发展"
    # 事件序：units → beats → selfcheck
    assert [k for k, _ in collector.events] == ["chapters_planned"] * 3
    assert [p["phase"] for _, p in collector.events] == ["units", "beats", "selfcheck"]
    assert collector.events[0][1]["units"] == 4
    assert collector.events[1][1]["ok"] == 1 and collector.events[1][1]["missing"] == []
    assert collector.events[2][1]["issues"] == 0


UNITS_8 = [unit(i, 1500, f"覆盖{i}", f"事件{i}", time=f"第{i}日", loc="山道")
           for i in range(1, 9)]


async def test_golden_8_units_1500_target_3000_four_chapters_semaphore(store, make_llm):
    """golden：8 段×1500 字、目标 3000 → 4 章；逐章 beats 并发上限 Semaphore(3)。"""
    def units_fn(prompt):
        assert "【剧情描述太少·补足】" not in prompt    # 8 段 ≥ min_units(3000)=4，不补足
        return units_payload(UNITS_8)

    def headers_fn(prompt):
        return headers_payload([(i, f"题{i}", f"目标{i}") for i in range(1, 5)])

    inner, calls = make_llm(make_router(units_fn, headers_fn))
    llm = SpyLLM(inner)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert llm.max_active == 3                       # 4 章并发恰好钉在 3
    assert "归组 4 章" in result.text
    chapters = store.list_chapters()
    assert [c.seq for c in chapters] == [1, 2, 3, 4]
    for i, ch in enumerate(chapters, 1):
        assert ch.meta["title"] == f"题{i}"
        assert ch.meta["covers"] == f"覆盖{2 * i - 1}；覆盖{2 * i}"
        assert ch.meta["time_span"] == f"第{2 * i - 1}日 → 第{2 * i}日"
        assert ch.meta["start_at"] == f"山道｜第{2 * i - 1}日"
        assert ch.meta["end_at"] == f"山道｜第{2 * i}日"
        assert [b["event"] for b in ch.meta["beats"]] == [f"第{i}章第{k}拍" for k in range(1, 6)]
    # units 1 + headers 1 + beats 4 + selfcheck 1 = 7 次调用
    assert len(calls) == 7
    assert result.usage == CompletionUsage(prompt_tokens=70, completion_tokens=140)


async def test_idempotent_second_run_skips_with_zero_llm_calls(store, make_llm):
    def router(body):
        return make_router(lambda p: units_payload(UNITS_8),
                           lambda p: headers_payload([(i, f"题{i}", f"目标{i}")
                                                      for i in range(1, 5)]))(body)

    llm, calls = make_llm(router)
    collector = Collector()
    first = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert "拆章完成" in first.text
    n_calls = len(calls)
    chapters_before = [c.meta for c in store.list_chapters()]

    second = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert "已拆 4 章" in second.text and "跳过" in second.text
    assert len(calls) == n_calls                     # 二次调用零新增 LLM
    assert [c.meta for c in store.list_chapters()] == chapters_before
    assert second.usage is None


def write_skeleton(store, seq, title, covers, units, target_words=3000):
    meta = {"seq": seq, "title": title, "objective": f"旧目标{seq}", "status": "planned",
            "beats": [], "covers": covers, "time_span": "旧夜",
            "start_at": "旧地｜旧夜", "end_at": "旧地｜旧夜",
            "plot_id": "1-2", "units": units, "target_words": target_words}
    store.write_chapter(ChapterDoc(seq=seq, meta=meta, body=""))
    return meta


async def test_backfill_only_beats_for_chapters_missing_beats(store, make_llm):
    """幂等门第二档：已有章节缺 beats → 只补 beats（零 units/headers 调用）+ selfcheck。"""
    slim = [slim_unit(unit(1, 1500, "旧覆盖一", "旧事件一"))]
    write_skeleton(store, 1, "旧章一", "旧覆盖一", slim)
    write_skeleton(store, 2, "旧章二", "旧覆盖二", slim)

    def forbidden(prompt):  # pragma: no cover - 不该被调到
        raise AssertionError("补 beats 不应触发 units/headers 调用")

    llm, calls = make_llm(make_router(forbidden, forbidden))
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2"})
    assert "缺节拍" in result.text and "补 beats" in result.text
    prompts = [b["messages"][0]["content"] for b in calls]
    assert all("【本章所属情节】" in p or "【章节清单（按序" in p for p in prompts)
    assert len(calls) == 3                           # beats×2 + selfcheck×1
    chapters = store.list_chapters()
    assert all(len(c.meta["beats"]) == 5 for c in chapters)
    assert all(c.meta["title"] == t for c, t in zip(chapters, ["旧章一", "旧章二"]))
    assert [p["phase"] for _, p in collector.events] == ["beats", "selfcheck"]


async def test_backfill_anchors_use_full_plot_siblings(store, make_llm):
    """续跑补拍分支：siblings 用本情节全部章节（非缺 beats 子集）——中章的上一章
    anchors 是真实邻章行，而不是「（本情节第一章）」退化文案。"""
    slim = [slim_unit(unit(1, 1500, "旧覆盖一", "旧事件一"))]
    write_skeleton(store, 1, "旧章一", "旧覆盖一", slim)
    write_skeleton(store, 2, "旧章二", "旧覆盖二", slim)
    write_skeleton(store, 3, "旧章三", "旧覆盖三", slim)
    for seq in (1, 3):                               # 首尾章已有 beats → 只补第 2 章
        ch = store.read_chapter(seq)
        ch.meta["beats"] = [{"event": "旧拍"}]
        store.write_chapter(ch)

    seen = {}

    def beats_fn(prompt):
        seen["prompt"] = prompt
        return beats_payload(5, 2)

    def forbidden(prompt):  # pragma: no cover - 不该被调到
        raise AssertionError("补 beats 不应触发 units/headers 调用")

    llm, calls = make_llm(make_router(forbidden, forbidden, beats_fn=beats_fn))
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2"})
    assert "补 beats" in result.text
    assert len(calls) == 2                           # beats×1 + selfcheck×1
    prompt = seen["prompt"]
    assert "上一章：第1章 旧章一｜覆盖：旧覆盖一" in prompt   # 真实前邻章
    assert "下一章：第3章 旧章三｜覆盖：旧覆盖三" in prompt   # 真实后邻章
    assert "（本情节第一章）" not in prompt
    assert "（本情节最后一章）" not in prompt


async def test_headers_failure_derives_titles_non_blocking(store, make_llm):
    """headers 两轮全坏 → 退化派生 title（首段 covers）/objective（段 event 串），不阻断拆章。"""
    def units_fn(prompt):
        return units_payload(UNITS_8)

    def beats_fn(prompt):
        m = re.search(r"【本章】第(\d+)章", prompt)
        return beats_payload(5, int(m.group(1)))

    llm, calls = make_llm(make_router(units_fn, lambda p: ("不是 JSON", U(1, 2)), beats_fn))
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert "拆章完成" in result.text                 # headers 失败不阻断
    chapters = store.list_chapters()
    for i, ch in enumerate(chapters, 1):
        assert ch.meta["title"] == f"覆盖{2 * i - 1}"[:20]      # derive_title：首段 covers
        assert ch.meta["objective"] == f"事件{2 * i - 1}；事件{2 * i}"  # derive_objective
    n_headers = sum(1 for b in calls if "【章节划分（分组已定" in b["messages"][0]["content"])
    assert n_headers == 2                            # complete_structured 内层扩额恰好两轮


async def test_selfcheck_fixes_unknown_seq_dropped_beats_kept_count(store, make_llm):
    """fixes：未知 seq 丢弃；title 剥「第N章」前缀；beats 修复 len(fix) >= len(cur) 才采纳
    （保拍数，逐拍替换 event、多出忽略）；四 anchor 键独立采纳。"""
    fix_payload = json.dumps({
        "ok": False,
        "issues": [{"kind": "logic", "seqs": [1], "detail": "凭空引用", "suggestion": "补前置"}],
        "fixes": [
            {"seq": 99, "title": "不存在的章"},                          # 未知 seq → 丢弃
            {"seq": 1, "title": "第3章 新题", "objective": "新目标",
             "covers": "城南破局", "time_span": "三日后",
             "start_at": "新城｜清晨", "end_at": "新城｜黄昏"},
            {"seq": 2, "beats": ["短1", "短2"]},                          # 2 < 5 → 保拍数丢弃
            {"seq": 1, "beats": [f"r{k}" for k in range(1, 8)]},          # 7 ≥ 5 → 采纳前 5 拍
        ],
        "need_web": [],
    }, ensure_ascii=False)

    def selfcheck_fn(prompt):
        return fix_payload, U(10, 20)

    def router(body):
        return make_router(lambda p: units_payload(UNITS_4W),
                           lambda p: headers_payload([(1, "题1", "目标1"),
                                                      (2, "题2", "目标2")]),
                           selfcheck_fn=selfcheck_fn)(body)

    llm, _ = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert "自查" in result.text and "问题 1 处" in result.text
    assert "丢弃未知 seq 1 条" in result.text
    ch1, ch2 = store.list_chapters()
    assert ch1.meta["title"] == "新题"               # strip_chapter_prefix 剥「第3章 」
    assert ch1.meta["objective"] == "新目标"
    assert ch1.meta["covers"] == "城南破局"
    assert ch1.meta["time_span"] == "三日后"
    assert ch1.meta["start_at"] == "新城｜清晨" and ch1.meta["end_at"] == "新城｜黄昏"
    assert [b["event"] for b in ch1.meta["beats"]] == [f"r{k}" for k in range(1, 6)]
    assert all(b["weight"] == 2 for b in ch1.meta["beats"])   # 逐拍替换 event、原权重保留
    assert [b["event"] for b in ch2.meta["beats"]] == [f"第2章第{k}拍" for k in range(1, 6)]
    assert ch2.meta["title"] == "题2"                # 未知 seq 修复未波及


async def test_selfcheck_still_repeat_noted_not_blocking(store, make_llm):
    """content_repeat 确定性复核：fixes 应用后相邻章 covers 仍重复 → 观测附「仍有重复：a/b」。"""
    fix_payload = json.dumps({
        "ok": False,
        "issues": [{"kind": "repeat", "seqs": [1, 2], "detail": "相邻重复", "suggestion": "错开"}],
        "fixes": [{"seq": 2, "covers": "覆盖1；覆盖2"}],   # 与 ch1 covers 完全相同
        "need_web": [],
    }, ensure_ascii=False)

    def selfcheck_fn(prompt):
        return fix_payload, U(10, 20)

    def router(body):
        return make_router(lambda p: units_payload(UNITS_4W),
                           lambda p: headers_payload([(1, "题1", "目标1"),
                                                      (2, "题2", "目标2")]),
                           selfcheck_fn=selfcheck_fn)(body)

    llm, _ = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2", "target_words": 3000})
    assert "仍有重复：1/2" in result.text
    ch1, ch2 = store.list_chapters()
    assert ch1.meta["covers"] == "覆盖1；覆盖2" and ch2.meta["covers"] == "覆盖1；覆盖2"
    assert "拆章完成" in result.text                 # 只提示不阻断


async def test_units_total_failure_observation_no_chapters(store, make_llm):
    """units 收缩两轮全坏 → 失败观测（bad_json），零章落盘，不 raise。"""
    llm, calls = make_llm(make_router(lambda p: ("不是 JSON", U(1, 2)),
                                      lambda p: ("不是 JSON", U(1, 2))))
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-2"})
    assert "失败" in result.text and "bad_json" in result.text
    assert store.list_chapters() == []
    assert len(calls) == 4                           # with_shrink 2 轮 × 内层扩额 2 次（同 T5 口径）
    assert result.usage == CompletionUsage(prompt_tokens=4, completion_tokens=8)


@pytest.mark.parametrize("bad", ["x", "0-1", "1-0", "1-", "1-2-3", "-1", "1-x", 7, None])
async def test_bad_plot_id_format_failure_observation(store, make_llm, bad):
    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("plot_id 不合法时不应触发 LLM 调用")

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector,
                            {} if bad is None else {"plot_id": bad})
    assert "失败" in result.text
    assert "vol}-{seq}" in result.text               # 格式要求如实报
    assert calls == [] and result.usage is None


async def test_unknown_plot_failure_observation(store, make_llm):
    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("未知情节不应触发 LLM 调用")

    llm, calls = make_llm(router)
    collector = Collector()
    missing = await run_tool(store, llm, collector, {"plot_id": "1-99"})
    assert "未找到" in missing.text and "1-99" in missing.text
    bad_vol = await run_tool(store, llm, collector, {"plot_id": "9-1"})
    assert "未找到" in bad_vol.text and "第 9 卷" in bad_vol.text
    assert calls == []


async def test_default_target_words_from_readme_or_default(store, make_llm):
    """args.target_words 缺省：本测试 README 无每章字数 → DEFAULT_TARGET_WORDS 落 meta。"""
    seen = {}

    def units_fn(prompt):
        seen["prompt"] = prompt
        return units_payload(UNITS_4)

    llm, _ = make_llm(make_router(units_fn,
                                  lambda p: headers_payload([(1, "题", "目标")])))
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-2"})
    ch = store.read_chapter(1)
    assert ch.meta["target_words"] == DEFAULT_TARGET_WORDS
    # README 无 planned_volumes → 回退 read_arcs 弧数（1 卷 → 100 章容量参考）
    assert "本卷约 100 章" in seen["prompt"]


async def test_arc_chapters_prefers_readme_planned_volumes(tmp_path, make_llm):
    """T6 口径裁定：arc_chapters 取 README planned_volumes 优先（seed :716-719），
    覆盖 context.py 的 read_arcs 口径——只进 plot_units 提示词容量参考。"""
    info = create_project(tmp_path, "卷数书",
                          story={"target_chapters": "100", "planned_volumes": "2"})
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                    "conflict": "c", "resolution": "r"}])
    s.upsert_plots(1, [dict(PLOT_1), dict(PLOT_2)])
    seen = {}

    def units_fn(prompt):
        seen["prompt"] = prompt
        return units_payload(UNITS_4)

    llm, _ = make_llm(make_router(units_fn,
                                  lambda p: headers_payload([(1, "题", "目标")])))
    collector = Collector()
    await run_tool(s, llm, collector, {"plot_id": "1-2", "target_words": 4000})
    assert "本卷约 50 章" in seen["prompt"]          # 100 ÷ 2，而非 read_arcs 的 100 ÷ 1


async def test_headers_prompt_carries_story_block(tmp_path, make_llm):
    """Minor：headers_vars 现在带 story——chapter_headers prompt 渲染出 README 题材/基调块
    （与 plot_units_vars/beats_vars 同口径，此前 {{story_block}} 恒空）。"""
    info = create_project(tmp_path, "设定书", story={"genre": "玄幻", "tone": "热血"})
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                    "conflict": "c", "resolution": "r"}])
    s.upsert_plots(1, [dict(PLOT_2)])
    seen = {}

    def headers_fn(prompt):
        seen["prompt"] = prompt
        return headers_payload([(1, "题", "目标")])

    llm, _ = make_llm(make_router(lambda p: units_payload(UNITS_4), headers_fn))
    collector = Collector()
    await run_tool(s, llm, collector, {"plot_id": "1-2", "target_words": 4000})
    assert "题材：玄幻" in seen["prompt"]
    assert "基调：热血" in seen["prompt"]
    assert "【创作基调（项目级设定，用户已定，必须遵守）】" in seen["prompt"]


def test_register_via_registry_returns_text(store, make_llm):
    def router(body):
        return make_router(lambda p: units_payload(UNITS_4),
                           lambda p: headers_payload([(1, "题", "目标")]))(body)

    llm, _ = make_llm(router)
    reg = ToolRegistry(store)
    register_chapter_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    assert reg.is_read_only("plan_chapters") is False
    obs = reg.execute("plan_chapters", {"plot_id": "1-2", "target_words": 4000},
                      allowed=frozenset({"plan_chapters"}))
    assert isinstance(obs, str) and "拆章完成" in obs   # registry 契约 str
    assert store.read_chapter(1).meta["title"] == "题"


async def test_registry_handler_refuses_inside_running_loop(store, make_llm):
    def router(body):
        return make_router(lambda p: units_payload(UNITS_4),
                           lambda p: headers_payload([(1, "题", "目标")]))(body)

    llm, _ = make_llm(router)
    reg = ToolRegistry(store)
    register_chapter_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    with pytest.raises(RuntimeError, match="await"):
        reg.execute("plan_chapters", {"plot_id": "1-2"}, allowed=frozenset({"plan_chapters"}))
