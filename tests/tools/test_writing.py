"""write_plot 写作五节点主体（章循环/逐拍流式/审核/落盘快照）测试。

MockTransport 统一网关：body["stream"]=True 走 SSE（复用 test_base._sse_body，勿自造
第二份），否则 JSON body；按模板锚点路由——【资源目录（可挑选的内容）】=context_select、
【待审正文】=draft_review、stream=逐拍写作。正文 chunk 逐段变编号（非严格周期），防
repeat_loop_cut 把 mock 长文当成模型重复循环截断。
"""
import json
import re
from pathlib import Path

import httpx
import pytest
from test_base import _sse_body  # 复用既有 SSE mock helper

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import count_words
from harness.tools.registry import ToolRegistry
from harness.tools.writing import register_writing_tools, write_plot


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


PLOT = {"seq": 1, "title": "开端", "summary": "概要一", "conflict": "冲突一",
        "resolution": "收束一", "status": "planned"}


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "写作书")
    s = ProjectStore(info.path)
    s.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                    "conflict": "c", "resolution": "r"}])
    s.upsert_plots(1, [dict(PLOT)])
    return s


@pytest.fixture
def make_llm():
    """router(body) -> str | (content, usage) | {"chunks","usage"?} | Exception；
    stream 请求走 SSE（复用 test_base._sse_body），非流式走 JSON body。"""
    def factory(router):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            item = router(body)
            if isinstance(item, Exception):
                raise item
            if body.get("stream"):
                chunks = item["chunks"] if isinstance(item, dict) else [item]
                usage = item.get("usage") if isinstance(item, dict) else None
                sse = _sse_body(*chunks, usage=usage)
                return httpx.Response(200, content=sse,
                                      headers={"Content-Type": "text/event-stream"})
            content, usage = item if isinstance(item, tuple) else (item, None)
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


class Collector:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))


def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def beat(event):
    return {"pov": "甲", "location": "山道", "event": event, "weight": 1}


def beats(*events):
    return [beat(e) for e in events]


def chapter(seq, title, beat_list, target=3000, plot_id="1-1"):
    meta = {"seq": seq, "title": title, "objective": f"目标{seq}", "status": "planned",
            "beats": beat_list, "covers": f"覆盖{seq}", "time_span": "夜",
            "start_at": "山道｜夜", "end_at": "山道｜夜",
            "plot_id": plot_id, "target_words": target}
    return ChapterDoc(seq=seq, meta=meta, body="")


def write_chapters(store, docs):
    for d in docs:
        store.write_chapter(d)


def draft_chunks(n_chars=1400):
    """非严格周期的长正文（逐段变编号）：repeat_loop_cut 要求 ≥24 字严格循环单元，
    变编号拼接不会命中熔断。两拍合计 ≈2800 字，落在 3000 字目标的 0.85× 护栏内。"""
    chunks, total, k = [], 0, 1
    while total < n_chars:
        c = f"第{k}段他沿着山道继续往前走，天色一点点暗下来，远处有鸟叫。"
        chunks.append(c)
        total += len(c)
        k += 1
    return chunks


SELECT_OK = json.dumps({"world_titles": [], "character_names": [],
                        "keywords": [], "need_web": []}, ensure_ascii=False)
REVIEW_PASS = json.dumps({"pass": True, "issues": []}, ensure_ascii=False)
EXTRACT_OK = json.dumps({"events": [], "character_events": [], "info_updates": [],
                         "promise_updates": [],
                         "state_updates": {"characters": [], "world": []}},
                        ensure_ascii=False)


def default_router(body):
    prompt = body["messages"][-1]["content"]
    if body.get("stream"):
        assert "【任务】" in prompt and "只写" in prompt   # 逐拍锁在场
        return {"chunks": draft_chunks(), "usage": U(5, 10)}
    if "【资源目录（可挑选的内容）】" in prompt:
        return SELECT_OK, U(1, 2)
    if "【待审正文】" in prompt:
        return REVIEW_PASS, U(3, 4)
    if "你是小说记忆整理员" in prompt:                      # task.memory_extract（P4 挂点）
        return EXTRACT_OK, U(2, 3)
    raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")


async def run_tool(store, llm, emit, args=None, settings=None):
    return await write_plot(store, llm, PromptLoader(home_dir=store.root),
                            settings or make_settings(), emit.emit, args or {})


def _markers() -> tuple:
    """分类契约瞬时词表全集（观测禁含任何成员，含中文同义词）。"""
    from harness.agent.loop import _EXTRA_TRANSIENT_MARKERS
    from harness.run import constants
    return constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS


def _assert_no_transient_markers(text: str) -> None:
    for marker in _markers():
        assert marker not in text, f"观测泄漏瞬时词 {marker!r}：{text!r}"


async def test_l2_crash_leaves_chapter_writing_not_done(store, make_llm, monkeypatch):
    """**L2**：写作链中途崩 → 章留在 **status="writing"**（不是 "done"）。

    原先第一次落盘就写 status="done"，而 ⑤ 修正 / ⑥ 去味 还没跑 →
    中途崩溃留下**半成品**却标着 done；而幂等门是「status=done 的章不重演」→
    **这一章永远不会被再处理**（独立审核 L2）。
    """
    import harness.writing.revise as revise_mod

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])

    async def boom(*args, **kwargs):
        raise RuntimeError("模拟进程被杀")

    # 崩溃点选 **deaify**（每章都跑）；revise 只在「有 blocking 问题」时才跑
    monkeypatch.setattr(revise_mod, "deaify_if_needed", boom)

    llm, _calls = make_llm(default_router)
    collector = Collector()
    with pytest.raises(RuntimeError):
        await run_tool(store, llm, collector, {"plot_id": "1-1"})

    doc = store.read_chapter(1)
    assert doc.meta.get("status") == "writing"     # 半成品**不能**标 done
    assert doc.meta.get("status") != "done"


async def test_p12_intent_block_in_writing_prompt(store, make_llm):
    """**P12**：写作链的**提示词里**有「本次意图与范围」块（目标/前提/限制/正文范围）。

    这是「意图」这一层在**落笔那一刻**第一次进上下文——此前只有规划链与验收链吃 brief。
    块在 **core** 段（预算裁剪只丢 droppable → core 永不丢）。
    """
    from harness.run.state import RunState

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    state = RunState.new("写个仙侠", ["outline"])
    state.brief.update({"goal": "写一部长篇仙侠", "premise": "少年修道成仙",
                         "constraints": ["每章 3000 字"], "write_target": "只写第一卷"})
    state.save(store.root)

    llm, calls = make_llm(default_router)
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-1"})

    prompts = [b["messages"][-1]["content"] for b in calls]
    joined = "\n".join(prompts)
    assert "【本次意图与范围（来自需求解析）】" in joined
    assert "目标：写一部长篇仙侠" in joined
    assert "前提：少年修道成仙" in joined
    assert "- 每章 3000 字" in joined
    assert "正文范围：只写第一卷" in joined
    # **不含**规划链那行（对写作是噪音）
    assert "只规划前" not in joined


async def test_p29_intent_block_in_review_not_in_revise_deaify(store, make_llm):
    """**P29/A3**（用户裁定：并进 review，不进修正/去味）：意图块进 **draft_review**
    提示词（含目标/正文范围行）；**修正/去味**提示词（若出现）一律不含。

    裁定理由：修正/去味只处理审稿已判出的具体问题（quote 定位），
    意图对它们是噪音；审出来的意见不得与意图相悖。
    """
    from harness.run.state import RunState

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    state = RunState.new("写个仙侠", ["outline"])
    state.brief.update({"goal": "写一部长篇仙侠", "premise": "少年修道成仙",
                         "constraints": ["每章 3000 字"], "write_target": "只写第一卷"})
    state.save(store.root)

    llm, calls = make_llm(default_router)
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-1"})

    HEADER = "【本次意图与范围（来自需求解析；审稿基准"
    review_prompts, fix_prompts = [], []
    for body in calls:
        prompt = body["messages"][-1]["content"]
        if "【待审正文】" in prompt:
            review_prompts.append(prompt)
        if "【待修正正文】" in prompt or "【待去味正文】" in prompt:
            fix_prompts.append(prompt)
    assert review_prompts, "写作链里没有 review 调用（测试前提崩了）"
    for prompt in review_prompts:                       # review（含 _recheck 复审）都有
        assert HEADER in prompt
        assert "目标：写一部长篇仙侠" in prompt
        assert "正文范围：只写第一卷" in prompt
    for prompt in fix_prompts:                          # 修正/去味（若被触发）一律没有
        assert HEADER not in prompt
        assert "目标：写一部长篇仙侠" not in prompt


async def test_p20_writing_prompt_reports_unfoldable_range(store, make_llm):
    """**P20**：正文范围**折算不成确定范围**（排除语义）→ 写作提示词里**如实说明**。

    写盘闸按**卷/章**拦；折算不出的范围（"不写第一卷"是排除，闭区间表达不了）→
    闸**不拦**（fail-open：用户要写却写不了，比拦错更糟）→ 但**必须说清楚**，
    否则用户的显式范围被静默丢掉（写作侧也不知道要按什么取舍）。
    """
    from harness.run.state import RunState

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    state = RunState.new("写个仙侠", ["outline"])
    state.brief.update({"goal": "写一部长篇仙侠",
                        "write_target": "不写第一卷"})
    state.save(store.root)

    llm, calls = make_llm(default_router)
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-1"})

    joined = "\n".join(b["messages"][-1]["content"] for b in calls)
    assert "正文范围：不写第一卷" in joined
    assert "无法折算成确定范围" in joined
    assert "不按范围拦截" in joined


async def test_p27_chapter_range_prompt_note_and_skip(store, make_llm):
    """**P27/A1**：章粒度范围折算得出 → ①提示词里明说「已按章拦截」②越界章逐章跳过。

    第 1 章在「第 2–5 章」区间外 → skip 行 + 原状不动；第 2 章在区间内 →
    正常进入写作流程，其提示词带「已按章拦截」注记。
    """
    from harness.run.state import RunState

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承")),
                           chapter(2, "题二", beats("二之起", "二之承"))])
    state = RunState.new("写个仙侠", ["outline"])
    state.brief.update({"goal": "写一部长篇仙侠",
                        "write_target": "从第二章写到第五章"})
    state.save(store.root)

    llm, calls = make_llm(default_router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})

    assert "第1章：不在本次写作范围（第 2–5 章）内，跳过" in result.text
    assert store.read_chapter(1).meta.get("status") == "planned"    # 越界章零写盘
    assert store.read_chapter(2).meta.get("status") == "done"       # 范围内章照写
    joined = "\n".join(b["messages"][-1]["content"] for b in calls)
    assert "已按章拦截：只写第 2–5 章" in joined


async def test_l2_half_written_chapter_is_retried_next_run(store, make_llm, monkeypatch):
    """**L2**：留下 status="writing" 的章 → **下次 run 会捡起来重做**（不跳过）。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    # 假装上一次崩在半路：章已是 writing、正文是半截
    doc = store.read_chapter(1)
    doc.meta["status"] = "writing"
    doc.body = "半截正文"
    store.write_chapter(doc)

    llm, calls = make_llm(default_router)
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-1"})

    # 这一章被重做了（有逐拍写入的调用），不是被跳过
    assert calls
    assert store.read_chapter(1).meta.get("status") == "done"


async def test_stream_starved_failure_line_uses_deterministic_label(store, make_llm):
    """两连 starved（finish=length 空聚合，扩额重试仍空聚合）→ 逐项失败行「无产出」、
    观测全文零瞬时词（truncated/starved 不得上屏）。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])

    def router(body):
        if body.get("stream"):
            return {"chunks": [], "finish": "length"}    # 空聚合 + finish=length → starved
        return default_router(body)

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert "第1章：未成功（无产出）" in result.text
    n_stream = sum(1 for b in calls if b.get("stream"))
    assert n_stream == 2                                 # starved ×2 封顶恰好两轮
    _assert_no_transient_markers(result.text)
    assert store.read_chapter(1).meta["status"] == "planned"   # 失败章不落盘


async def test_review_timeout_note_uses_call_failed_label(store, make_llm):
    """complete_structured 路径 httpx.ReadTimeout → 成功行注「审核未完成（调用失败）」、
    观测全文零瞬时词；审核超时降级不阻断落盘。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])

    def router(body):
        prompt = body["messages"][-1]["content"]
        if "【待审正文】" in prompt:
            return httpx.ReadTimeout("boom")
        return default_router(body)

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert "审核未完成（调用失败）" in result.text
    n_review = sum(1 for b in calls
                   if not b.get("stream")
                   and "【待审正文】" in b["messages"][-1]["content"])
    assert n_review == 1                                 # timeout 不重试
    _assert_no_transient_markers(result.text)
    assert store.read_chapter(1).meta["status"] == "done"


def test_reason_labels_disjoin_transient_markers():
    """观测 reason 映射表的值 ∩ 瞬时词表 == ∅（分类契约：观测词表零泄漏）。"""
    from harness.tools import writing
    assert set(writing._REASON_LABELS.values()) & set(_markers()) == set()


FLAVOR_MIDDLES = ["明亮", "晃眼", "刺眼", "灼目", "白炽", "耀眼",
                  "晃荡", "雪亮", "眩目", "烁金", "煌煌", "烨烨"]
FLAVOR_UNITS = "".join(f"那么{m}，那么刺眼。" for m in FLAVOR_MIDDLES)


async def test_blocking_revise_and_deaify_snapshot_reflects_final(store, make_llm):
    """blocking → revise 一轮收敛 → deaify 定点落地：快照 deaified=True 且
    flavor_score/flavor_items 是对终稿独立 detect 的值（去味前旧分不得进快照）；
    逐章末行 verdict 用复审后结论（如实显示通过）。mock 模式同 tests/writing/test_revise.py。"""
    from harness.tools.writing import _flavor_items
    from harness.writing.flavor import detect

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    beat1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4 + FLAVOR_UNITS
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    beat1_new = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4 + FLAVOR_UNITS
    state = {"draft_stream": 0, "review": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            if "【要改的这一拍】" in prompt:               # revise 按拍重写
                return {"chunks": [beat1_new], "usage": U(5, 10)}
            assert "【任务】" in prompt and "只写" in prompt
            which = state["draft_stream"]
            state["draft_stream"] += 1
            return {"chunks": [beat1 if which == 0 else beat2], "usage": U(5, 10)}
        if "【待审正文】" in prompt:
            state["review"] += 1
            if state["review"] == 1:                        # 初审：1 条 blocking（rewrite 类）
                issue = {"type": "logic", "fix_kind": "rewrite", "quote": "甲沿着山道",
                         "issue": "节奏拖沓", "suggestion": "加快"}
                return json.dumps({"pass": False, "issues": [issue]}, ensure_ascii=False), U(3, 4)
            return json.dumps({"pass": True, "issues": []}, ensure_ascii=False), U(3, 4)  # 复审通过
        if "【被点名的片段】" in prompt:                     # deaify 定点：12 条全落地
            edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
            return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "你是小说记忆整理员" in prompt:                  # 记忆提取（初写/revise/deaify 后各一次）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    llm, _calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    doc = store.read_chapter(1)
    assert doc.meta["status"] == "done"
    assert doc.body.count("天光大亮。") == 12              # deaify 定点落地生效
    assert "beat_spans" not in doc.meta                   # 去味后区间作废（deaify 就地清）
    snap = json.loads((store.root / ".harness" / "review" / "1.json")
                      .read_text(encoding="utf-8"))
    assert snap["deaified"] is True                       # 快照声称已去味
    assert snap["revise_count"] == 1 and snap["pass_review"] is True
    final_report = detect(doc.body)                       # 对终稿独立重算
    assert snap["flavor_score"] == final_report.score     # 分数必须是去味后的（旧实现是去味前旧分）
    assert snap["flavor_items"] == _flavor_items(final_report)
    assert "审核通过" in result.text and "未通过" not in result.text   # verdict 取复审后结论
    kinds = [k for k, _ in collector.events]
    assert "chapter_revised" in kinds
    revised = [p for k, p in collector.events if k == "chapter_revised"]
    assert revised[0]["pass_review"] is True and revised[0]["revise_count"] == 1


async def test_write_three_chapters_happy_path(store, make_llm):
    """3 章全 pass → 三文件 status=done、word_count 实填、快照在、事件序、观测逐章行。"""
    write_chapters(store, [
        chapter(1, "题一", beats("一之起", "一之承")),
        chapter(2, "题二", beats("二之起", "二之承")),
        chapter(3, "题三", beats("三之起", "三之承")),
    ])
    llm, calls = make_llm(default_router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert "写作完成" in result.text and "成功 3 章" in result.text
    for seq, title in ((1, "题一"), (2, "题二"), (3, "题三")):
        doc = store.read_chapter(seq)
        assert doc.meta["status"] == "done"
        assert doc.meta["word_count"] == count_words(doc.body)
        assert doc.body                                    # 正文实填
        snap = store.root / ".harness" / "review" / f"{seq}.json"
        assert snap.exists()
        data = json.loads(snap.read_text(encoding="utf-8"))
        assert data["pass_review"] is True
        assert data["issues"] == []
        assert data["revise_count"] == 0 and data["deaified"] is False
        assert isinstance(data["flavor_score"], (int, float))
        assert isinstance(data["flavor_items"], list)
        assert data["at"]
        assert f"第{seq}章「{title}」" in result.text       # 观测逐章行
    # 事件序：逐章串行 chapter_start→chapter_done（draft_progress 节流帧穿插其间）
    kinds = [k for k, _ in collector.events if k != "draft_progress"]
    assert kinds == ["chapter_start", "chapter_done"] * 3
    progress = [p for k, p in collector.events if k == "draft_progress"]
    assert progress and all(set(p) == {"seq", "beat", "chars", "phase"} for p in progress)
    assert all(p["phase"] == "draft" and p["chars"] > 0 for p in progress)
    for kind, payload in collector.events:
        if kind == "chapter_start":
            assert payload["target_words"] == 3000
        elif kind == "chapter_done":
            assert payload["pass_review"] is True
            assert payload["words"] > 0
            assert payload["memory_status"] == "extracted"
    # 每章 select 1 + beats 2 + review 1 + 记忆提取 1：流式 6 + 非流式 9
    n_stream = sum(1 for b in calls if b.get("stream"))
    assert n_stream == 6 and len(calls) - n_stream == 9
    assert result.usage is not None


async def test_review_bad_json_defaults_pass_but_detect_runs(store, make_llm):
    """review 返坏 JSON → 不崩、逻辑 pass（扩额重试两轮后按无问题处理），detect 分仍进快照。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])

    def router(body):
        prompt = body["messages"][-1]["content"]
        if "【待审正文】" in prompt:
            return ("不是 JSON", U(3, 4))
        return default_router(body)

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    doc = store.read_chapter(1)
    assert doc.meta["status"] == "done"                    # 审核坏了不阻断落盘
    data = json.loads((store.root / ".harness" / "review" / "1.json")
                      .read_text(encoding="utf-8"))
    assert data["pass_review"] is True
    assert data["issues"] == []
    assert isinstance(data["flavor_score"], (int, float))  # detect 照常跑、进快照
    n_review = sum(1 for b in calls
                   if not b.get("stream")
                   and "【待审正文】" in b["messages"][-1]["content"])
    assert n_review == 2                                   # 解析失败扩额恰好重试一次
    assert "审核未完成" in result.text                        # 观测如实注


async def test_mid_chapter_stream_failure_stops_cleanly(store, make_llm):
    """第 2 章第 1 拍 api 错 → 第 1 章完好、后续章骨架原样不动、逐项失败行带确定性分类。"""
    write_chapters(store, [
        chapter(1, "题一", beats("一之起", "一之承")),
        chapter(2, "题二", beats("二之起", "二之承")),
        chapter(3, "题三", beats("三之起", "三之承")),
    ])
    state = {"stream": 0}

    def router(body):
        if body.get("stream"):
            state["stream"] += 1
            if state["stream"] == 3:                       # 第 2 章第 1 拍
                return httpx.ConnectError("boom")
            return {"chunks": draft_chunks(), "usage": U(5, 10)}
        return default_router(body)

    llm, _calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    one = store.read_chapter(1)
    assert one.meta["status"] == "done" and one.body       # 第 1 章完好
    for seq in (2, 3):                                     # 后续章不动：骨架原样
        doc = store.read_chapter(seq)
        assert doc.meta["status"] == "planned"
        assert doc.body == ""
        assert "word_count" not in doc.meta
        assert not (store.root / ".harness" / "review" / f"{seq}.json").exists()
    assert "第2章：未成功（调用失败）" in result.text      # 逐项行 + 确定性类别（瞬时词禁上屏）
    assert "成功 1 章" in result.text and "失败 1 章" in result.text
    assert result.usage is not None                        # 失败前的调用用量仍记账


async def test_no_beats_chapter_skipped_and_noted(store, make_llm):
    write_chapters(store, [
        chapter(1, "题一", beats("一之起")),
        chapter(2, "题二", []),
        chapter(3, "题三", beats("三之起")),
    ])
    llm, calls = make_llm(default_router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert "第2章：无细纲，跳过" in result.text
    assert "跳过 1 章" in result.text
    assert store.read_chapter(1).meta["status"] == "done"
    assert store.read_chapter(3).meta["status"] == "done"
    two = store.read_chapter(2)                            # 跳过的章原样保留
    assert two.meta["status"] == "planned" and two.body == ""
    n_stream = sum(1 for b in calls if b.get("stream"))
    assert n_stream == 2                                   # 只写了两章的正文


async def test_seq_range_clamp_reported(store, make_llm):
    """seq_from/seq_to 与待写章集取交集，越界夹取并在观测首行如实说明。"""
    write_chapters(store, [
        chapter(3, "题三", beats("三之起")),
        chapter(4, "题四", beats("四之起")),
        chapter(5, "题五", beats("五之起")),
    ])
    llm, _calls = make_llm(default_router)
    collector = Collector()
    result = await run_tool(store, llm, collector,
                            {"plot_id": "1-1", "seq_from": 1, "seq_to": 4})
    assert "seq 1~4 越界" in result.text                   # 首行如实报夹取
    assert "夹取为 3~4" in result.text
    assert store.read_chapter(3).meta["status"] == "done"
    assert store.read_chapter(4).meta["status"] == "done"
    five = store.read_chapter(5)                           # 范围外不动
    assert five.meta["status"] == "planned" and five.body == ""


async def test_seq_range_outside_reports_note_in_empty_failure(store, make_llm):
    """seq_from 整段在待写章之上（>hi，「全在范围外」越界）→ note 照样产生并拼进
    「无产出」失败行（M-5：交集为空不得丢夹取说明）。"""
    write_chapters(store, [
        chapter(3, "题三", beats("三之起")),
        chapter(4, "题四", beats("四之起")),
    ])
    llm, _calls = make_llm(default_router)
    collector = Collector()
    result = await run_tool(store, llm, collector,
                            {"plot_id": "1-1", "seq_from": 6})
    assert "seq 6~ 越界" in result.text
    assert "无交集" in result.text
    assert "write_plot 失败（无产出）" in result.text
    assert store.read_chapter(3).meta["status"] == "planned"   # 零章被写


async def test_pass_not_serious_skips_deaify(store, make_llm):
    """pass + detect 不 serious → deaify_if_needed 内部触发判定拦截：零额外 LLM 调用、
    快照 deaified=False、正文原样。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    llm, calls = make_llm(default_router)
    collector = Collector()
    await run_tool(store, llm, collector, {"plot_id": "1-1"})
    snap = json.loads((store.root / ".harness" / "review" / "1.json")
                      .read_text(encoding="utf-8"))
    assert snap["deaified"] is False
    n_stream = sum(1 for b in calls if b.get("stream"))
    assert n_stream == 2 and len(calls) == 5               # select 1 + stream 2 + review 1 + 提取 1


async def test_memory_extract_failure_noted_but_chapter_succeeds(store, make_llm):
    """提取失败（坏 JSON 扩额两轮）不阻断章节成功：章照常落盘、观测注「记忆提取失败」、
    chapter_done memory_status="failed"、观测全文零瞬时词（reason 过映射表）。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])

    def router(body):
        prompt = body["messages"][-1]["content"]
        if "你是小说记忆整理员" in prompt:
            return "不是 JSON"
        return default_router(body)

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert store.read_chapter(1).meta["status"] == "done"   # 章成功不阻断
    assert "记忆提取失败" in result.text
    _assert_no_transient_markers(result.text)
    done = [p for k, p in collector.events if k == "chapter_done"]
    assert done[0]["memory_status"] == "failed"
    n_extract = sum(1 for b in calls
                    if "你是小说记忆整理员" in b["messages"][-1]["content"])
    assert n_extract == 2                                   # 解析失败扩额恰好重试一次
    assert not (store.root / "memory" / "0001.md").exists()  # 失败不留半套记忆


async def test_memory_extract_reruns_after_revise_and_deaify(store, make_llm):
    """裁定 2：改写后自动重提取（复用同一调用点）——初写 + revise 重落盘 + deaify
    重落盘共三次提取；先清后建幂等：最终记忆文件只含最后一次提取的条目。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    beat1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4 + FLAVOR_UNITS
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    beat1_new = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4 + FLAVOR_UNITS
    state = {"draft": 0, "review": 0, "extract": 0}

    def extract_payload(n):
        return json.dumps({"events": [{"title": f"提取{n}号", "summary": "s",
                                       "location": "山寨", "time_marker": "",
                                       "characters": [], "new_info": [],
                                       "foreshadow": ""}],
                           "character_events": [], "info_updates": [],
                           "promise_updates": [],
                           "state_updates": {"characters": [], "world": []}},
                          ensure_ascii=False)

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            if "【要改的这一拍】" in prompt:               # revise 按拍重写
                return {"chunks": [beat1_new], "usage": U(5, 10)}
            which = state["draft"]
            state["draft"] += 1
            return {"chunks": [beat1 if which == 0 else beat2], "usage": U(5, 10)}
        if "【待审正文】" in prompt:
            state["review"] += 1
            if state["review"] == 1:                        # 初审：1 条 blocking（rewrite 类）
                issue = {"type": "logic", "fix_kind": "rewrite", "quote": "甲沿着山道",
                         "issue": "节奏拖沓", "suggestion": "加快"}
                return json.dumps({"pass": False, "issues": [issue]}, ensure_ascii=False), U(3, 4)
            return REVIEW_PASS, U(3, 4)                     # 复审通过
        if "【被点名的片段】" in prompt:                     # deaify 定点：12 条全落地
            edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
            return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "你是小说记忆整理员" in prompt:                  # 三次提取各返回不同措辞
            state["extract"] += 1
            return extract_payload(state["extract"]), U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    llm, _calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert store.read_chapter(1).meta["status"] == "done"
    assert state["extract"] == 3                            # 初写 + revise + deaify 各一次
    text = (store.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert text.count("- [全知] ") == 1                     # 第二/三次 purge 生效：无堆积
    assert "提取3号" in text
    assert "提取1号" not in text and "提取2号" not in text
    assert "记忆提取失败" not in result.text                 # 三次全成功，无失败注


@pytest.mark.parametrize("bad", ["x", "0-1", "1-0", "1-", "1-2-3", "-1", "1-x", 7, None])
async def test_bad_plot_id_format_failure_observation(store, make_llm, bad):
    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("plot_id 不合法时不应触发 LLM 调用")

    llm, calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector,
                            {} if bad is None else {"plot_id": bad})
    assert "write_plot 失败（参数）" in result.text
    assert "vol}-{seq}" in result.text
    assert calls == [] and result.usage is None


def test_register_via_registry_returns_text(store, make_llm):
    llm, _calls = make_llm(default_router)
    reg = ToolRegistry(store)
    register_writing_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    assert reg.is_read_only("write_plot") is False
    write_chapters(store, [chapter(1, "题一", beats("一之起"))])
    obs = reg.execute("write_plot", {"plot_id": "1-1"},
                      allowed=frozenset({"write_plot"}))
    assert isinstance(obs, str) and "写作完成" in obs      # registry 契约 str
    assert store.read_chapter(1).meta["status"] == "done"


# ---------------------------------------------------------------- 卷级 digest 挂点（P4 Task 6）

def _spy_keeper(calls: list) -> type:
    """DigestKeeper spy：记录 maintain 调用入参，实现走真身（模块级 monkeypatch 目标）。"""
    from harness.memory.digest import DigestKeeper

    class SpyKeeper(DigestKeeper):
        async def maintain(self, **kwargs):
            calls.append(kwargs)
            return await super().maintain(**kwargs)

    return SpyKeeper


DIGEST_ANCHOR = "你是小说前情提要压缩员"   # task.digest_compress 模板锚点


async def test_digest_maintained_exactly_once_when_plot_completes(store, make_llm, monkeypatch):
    """挂点正向：一个 plot 两章全过 → maintain 恰好一次（不是每章各一次）；
    digest 文件一节、两章段、事件行投影自章记忆。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承")),
                           chapter(2, "题二", beats("二之起", "二之承"))])

    def router(body):
        prompt = body["messages"][-1]["content"]
        if "你是小说记忆整理员" in prompt:                  # 记忆提取带一条事件（digest 投影源）
            return json.dumps({
                "events": [{"title": "山道夜行", "summary": "甲赶路",
                            "location": "山道", "time_marker": "夜",
                            "characters": [], "new_info": [], "foreshadow": ""}],
                "character_events": [], "info_updates": [], "promise_updates": [],
                "state_updates": {"characters": [], "world": []}}, ensure_ascii=False), U(2, 3)
        return default_router(body)

    calls: list = []
    monkeypatch.setattr("harness.tools.writing.DigestKeeper", _spy_keeper(calls))
    llm, _calls = make_llm(router)
    collector = Collector()
    result = await run_tool(store, llm, collector, {"plot_id": "1-1"})
    assert "写作完成" in result.text and "成功 2 章" in result.text
    assert "维护失败" not in result.text
    assert len(calls) == 1                                  # 恰好一次：第二章完成后才触发
    assert calls[0]["vol"] == 1 and calls[0]["plot_seq"] == 1
    assert calls[0]["chapter_seqs"] == [1, 2]
    digest = store.root / "memory" / "digests" / "vol-01.md"
    text = digest.read_text(encoding="utf-8")
    assert text.count("## 情节 1. 开端") == 1               # 一节（本 plot 全章）
    assert "### 第1章《题一》" in text and "### 第2章《题二》" in text
    assert "- 山道夜行——甲赶路（山道；夜）" in text          # 事件行自章记忆投影


async def test_digest_not_maintained_when_chapter_fails(store, make_llm, monkeypatch):
    """挂点负向：第二章 starved 失败 → 循环 break，该 plot 仍有 status!=done 章
    → maintain 不触发、digest 文件不产生。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承")),
                           chapter(2, "题二", beats("二之起", "二之承"))])
    state = {"stream": 0}

    def router(body):
        if body.get("stream"):
            state["stream"] += 1
            if state["stream"] <= 2:                        # 第一章两拍
                return {"chunks": draft_chunks(), "usage": U(5, 10)}
            return {"chunks": [], "finish": "length"}       # 第二章一拍即 starved
        return default_router(body)

    calls: list = []
    monkeypatch.setattr("harness.tools.writing.DigestKeeper", _spy_keeper(calls))
    llm, _calls = make_llm(router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "第2章：未成功（无产出）" in result.text
    assert calls == []                                      # 失败章不触发 maintain
    assert not (store.root / "memory" / "digests" / "vol-01.md").exists()


async def test_digest_maintain_failure_noted_but_not_blocking(store, make_llm, monkeypatch):
    """挂点降级：maintain 内部异常（压缩链非 LLMError 类错误按 fail-loud 口径上抛）
    → write_plot 整体仍成功、观测如实注记（不阻断）、digest 文件无损坏（先清后建未
    完成，原文原样）。"""
    from harness.memory.digest import DigestKeeper

    class BoomKeeper(DigestKeeper):
        async def maintain(self, **kwargs):
            calls.append(kwargs)
            return await super().maintain(**kwargs)

        async def _compress_section(self, **kwargs):    # 压缩链意外错误（非 LLMError）
            raise RuntimeError("digest_compress boom")

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    digests = store.root / "memory" / "digests"
    digests.mkdir(parents=True)
    old_body = "- 旧事件——" + "旧" * 2200                     # 预置旧节：追加新节后超 2000 字
    seeded = f"# 第1卷前情提要\n\n## 情节 9. 旧节\n\n{old_body}\n"
    (digests / "vol-01.md").write_text(seeded, encoding="utf-8")

    calls: list = []
    monkeypatch.setattr("harness.tools.writing.DigestKeeper", BoomKeeper)
    llm, _calls = make_llm(default_router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "写作完成" in result.text and "成功 1 章" in result.text   # 章节成功不受反噬
    assert "卷级前情提要维护失败（情节 1-1）" in result.text
    assert "digest 未更新（不阻断）" in result.text
    _assert_no_transient_markers(result.text)
    assert len(calls) == 1                                  # 触发过一次、失败后未重试
    assert (digests / "vol-01.md").read_text(encoding="utf-8") == seeded   # 文件无损坏


async def test_digest_compress_bad_json_degrades_verbose_not_blocking(store, make_llm):
    """压缩 LLM 产出不可解析（complete 返 None，keeper 内部降级）→ 无注记、
    digest 宁可冗长不可丢（旧节原文保留 + 新节仍追加）、write_plot 整体成功。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    digests = store.root / "memory" / "digests"
    digests.mkdir(parents=True)
    old_body = "- 旧事件——" + "旧" * 2200
    (digests / "vol-01.md").write_text(
        f"# 第1卷前情提要\n\n## 情节 9. 旧节\n\n{old_body}\n", encoding="utf-8")

    def router(body):
        if DIGEST_ANCHOR in body["messages"][-1]["content"]:
            return "不是 JSON", U(2, 3)                     # 两轮 bad_json → None → 降级
        return default_router(body)

    llm, _calls = make_llm(router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "写作完成" in result.text and "成功 1 章" in result.text
    assert "卷级前情提要维护失败" not in result.text          # 内部降级不冒注（日志已记）
    text = (digests / "vol-01.md").read_text(encoding="utf-8")
    assert old_body in text                                 # 旧节原文未丢
    assert "（已压缩）" not in text                            # 未压缩
    assert "## 情节 1. 开端" in text                           # 新节照常追加


# ================================================================ P4 Task 7 修复轮：索引精确失效（评审 Important-1）

async def test_corpus_index_built_once_then_path_refreshed(store, make_llm, monkeypatch):
    """全量 build_corpus 只跑一次（CPU 密集走 to_thread，不逐章重建堵事件循环）；
    章后仅 path 级精确失效+重建（_refresh_corpus_paths 每成功章一次）。"""
    from harness.tools import writing as writing_mod
    write_chapters(store, [
        chapter(1, "题一", beats("一之起", "一之承")),
        chapter(2, "题二", beats("二之起", "二之承")),
    ])
    builds: list = []
    real_build = writing_mod.build_corpus

    def spy_build(root):
        builds.append(root)
        return real_build(root)

    refreshes: list = []
    real_refresh = writing_mod._refresh_corpus_paths

    def spy_refresh(store_, index_, seq):
        refreshes.append(seq)
        return real_refresh(store_, index_, seq)

    monkeypatch.setattr(writing_mod, "build_corpus", spy_build)
    monkeypatch.setattr(writing_mod, "_refresh_corpus_paths", spy_refresh)
    llm, _calls = make_llm(default_router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "成功 2 章" in result.text
    assert len(builds) == 1                      # 一次全量，非每章重建
    assert refreshes == [1, 2]                   # 每成功章一次 path 级刷新


def test_refresh_corpus_paths_replaces_only_target_chapter(store):
    """增量失效同一性：写第 2 章不重建第 1 章语料项（对象同一性），第 2 章项被替换。"""
    from harness.memory import MemoryStore
    from harness.retrieval.corpus import Index, build_corpus
    from harness.tools.writing import _refresh_corpus_paths

    write_chapters(store, [
        chapter(1, "题一", beats("一之起")),
        chapter(2, "题二", beats("二之起")),
    ])
    mem = MemoryStore(store.root)
    mem.write_chapter_memory(1, "题一", {"events": [
        {"title": "旧事件", "summary": "第一章的旧事件", "location": "",
         "time_marker": "", "characters": []}]})
    mem.write_chapter_memory(2, "题二", {"events": [
        {"title": "旧事件二", "summary": "第二章的旧事件", "location": "",
         "time_marker": "", "characters": []}]})
    index = Index(build_corpus(store.root))
    before = {it.id: it for it in index.items}
    ids1 = {id(it) for it in index.items
            if it.id.startswith(("chap:0001", "mem:0001:"))}
    assert ids1 and len(before) == len(index.items)

    # 改第 2 章的记忆与正文 → 只刷新第 2 章的两个 path
    mem.write_chapter_memory(2, "题二", {"events": [
        {"title": "新事件", "summary": "第二章改写后的新事件", "location": "",
         "time_marker": "", "characters": []}]})
    doc2 = store.read_chapter(2)
    doc2.body = "第二章改写后的正文"
    store.write_chapter(doc2)
    _refresh_corpus_paths(store, index, 2)

    after = {it.id: it for it in index.items}
    # 第 1 章语料项对象未被替换（同一性——零重建的直接证据）
    assert {id(it) for it in index.items
            if it.id.startswith(("chap:0001", "mem:0001:"))} == ids1
    # 第 2 章项被替换：新对象 + 新内容
    ev2 = [it for it in index.items if it.id == "mem:0002:事件:1"]
    assert len(ev2) == 1
    assert "第二章改写后的新事件" in ev2[0].text
    assert id(ev2[0]) != id(before["mem:0002:事件:1"])
    ch2 = [it for it in index.items if it.id == "chap:0002"]
    assert len(ch2) == 1 and "第二章改写后的正文" in ch2[0].text
    # 全集 id 稳定：无丢失、无重复
    assert sorted(after) == sorted(before)


def test_refresh_corpus_paths_clears_removed_memory(store):
    """章记忆被清（purge）后刷新 → 该章 mem: 语料项从索引消失（path 失效不留残）。"""
    from harness.memory import MemoryStore
    from harness.retrieval.corpus import Index, build_corpus
    from harness.tools.writing import _refresh_corpus_paths

    write_chapters(store, [chapter(1, "题一", beats("一之起"))])
    mem = MemoryStore(store.root)
    mem.write_chapter_memory(1, "题一", {"events": [
        {"title": "旧事件", "summary": "即将被清掉的事件", "location": "",
         "time_marker": "", "characters": []}]})
    index = Index(build_corpus(store.root))
    assert any(it.id.startswith("mem:0001:") for it in index.items)
    mem.purge_chapter(1)
    _refresh_corpus_paths(store, index, 1)
    assert not any(it.id.startswith("mem:0001:") for it in index.items)
    assert any(it.id == "chap:0001" for it in index.items)   # 正文 path 不受影响


def test_selected_of_keeps_keywords():
    """P4 Task 8：context_select 模板的 keywords 键不再丢弃（回指通道查询词源之一）。"""
    from harness.tools.writing import _selected_of

    got = _selected_of({"world_titles": ["山门"], "character_names": ["陆昭"],
                        "keywords": ["青铜钥匙", "  ", "云纹"], "need_web": ["x"]})
    assert got["keywords"] == ["青铜钥匙", "云纹"]
    assert got["world_titles"] == ["山门"]
    assert got["character_names"] == ["陆昭"]
    assert _selected_of({"keywords": "not-a-list"})["keywords"] == []
    assert _selected_of(None)["keywords"] == []


async def test_backref_block_injected_into_draft_context(store, make_llm):
    """①b 伏笔回指块：已写章正文命中 context_select keywords → 逐拍/审核 ctx.user
    尾部注入【伏笔回指】块，块内引文逐字存在于该章正文（locate_quote 回验口径）。"""
    from harness.text import locate_quote

    ch1_body = "山道夜雨。\n\n陆昭捡到了一枚青铜钥匙，钥匙上刻着云纹。"
    write_chapters(store, [
        ChapterDoc(seq=1, meta={"seq": 1, "title": "题一", "status": "done",
                                "plot_id": "1-1"}, body=ch1_body),
        chapter(2, "题二", beats("二之起：他想起那枚青铜钥匙", "二之承")),
    ])
    select_kw = json.dumps({"world_titles": [], "character_names": [],
                            "keywords": ["青铜钥匙"], "need_web": []},
                           ensure_ascii=False)

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            return {"chunks": draft_chunks(), "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return select_kw, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "你是小说记忆整理员" in prompt:
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    llm, calls = make_llm(router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "成功 1 章" in result.text
    hit = 0
    for body in calls:
        if not body.get("stream"):
            continue
        text = "\n".join(m.get("content") or "" for m in body["messages"])
        m = re.search(r"【伏笔回指】\n· 第1章《题一》：「(.+?)」", text)
        if m:
            hit += 1
            assert locate_quote(ch1_body, m.group(1)) is not None   # Review Focus #5
    assert hit >= 2          # 逐拍 ctx 与审核 ctx 均注入（两拍 + 审核复用同 ctx）


# ================================================================ P4 终审修复：to_thread 纪律回流（I-1）

async def test_bundle_built_once_per_chapter(store, make_llm, monkeypatch):
    """I-1b：bundle 每章只建一次（拍循环共享）——_gather 不再每拍重读全书记忆。

    spy 计数：两拍章 build_draft_context 跑 ≥2 次而 build_memory_bundle 只跑 1 次。
    """
    from harness.tools import writing as writing_mod
    from harness.writing import context as ctx_mod

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    bundles: list = []
    real_bundle = ctx_mod.build_memory_bundle

    def spy_bundle(**kw):
        bundles.append(kw.get("cur_seq"))
        return real_bundle(**kw)

    dcs: list = []
    real_dc = writing_mod.build_draft_context

    def spy_dc(*a, **kw):
        dcs.append(1)
        return real_dc(*a, **kw)

    monkeypatch.setattr(ctx_mod, "build_memory_bundle", spy_bundle)
    monkeypatch.setattr(writing_mod, "build_draft_context", spy_dc)
    llm, _calls = make_llm(default_router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    assert "成功 1 章" in result.text
    assert len(bundles) == 1                    # 两拍章只建一次 bundle
    assert len(dcs) >= 2                        # build_draft_context 每拍照跑（共享 bundle）


async def test_backrefs_offloaded_off_event_loop(store, make_llm, monkeypatch):
    """I-1a：backrefs（同步 BM25 全语料分词）经 to_thread 调用——worker 线程无事件
    循环，慢调用期间事件循环不冻结（照 tests/retrieval/test_corpus.py 的 wait_for 手法）。"""
    import asyncio as _asyncio
    import time as _time

    from harness.tools import writing as writing_mod

    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    seen: dict = {}
    real = writing_mod.backrefs

    def slow_backrefs(*a, **kw):
        try:
            _asyncio.get_running_loop()
            seen["loop"] = True                 # 直调=在事件循环里（违例）
        except RuntimeError:
            seen["loop"] = False                # to_thread=worker 线程（合规）
        _time.sleep(0.15)
        return real(*a, **kw)

    monkeypatch.setattr(writing_mod, "backrefs", slow_backrefs)
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await _asyncio.sleep(0.02)
            ticks += 1

    llm, _calls = make_llm(default_router)
    task = _asyncio.create_task(run_tool(store, llm, Collector(), {"plot_id": "1-1"}))
    await ticker()
    result = await _asyncio.wait_for(task, timeout=30)
    assert seen.get("loop") is False            # worker 线程里跑（无事件循环）
    assert ticks == 5                           # 慢调用期间循环照常跑（未被冻结）
    assert "成功 1 章" in result.text


# ================================================================ P5 Task 4：子调用 usage 入账 + 观测文案不变

async def test_write_plot_usage_includes_extraction(store, make_llm):
    """记忆提取那次 LLM 调用的 usage 并入 ToolResult.usage（提取子调用原先被丢弃）。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    llm, _calls = make_llm(default_router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    # select 1×U(1,2) + 逐拍 2×U(5,10) + review 1×U(3,4) + 记忆提取 1×U(2,3)
    assert result.usage is not None
    assert result.usage.prompt_tokens == 1 + 5 + 5 + 3 + 2
    assert result.usage.completion_tokens == 2 + 10 + 10 + 4 + 3


async def test_write_plot_usage_includes_revise_and_deaify(store, make_llm):
    """revise / deaify 的子调用 usage 也并入 ToolResult.usage（各段之和）。"""
    write_chapters(store, [chapter(1, "题一", beats("一之起", "一之承"))])
    beat1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4 + FLAVOR_UNITS
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    beat1_new = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4 + FLAVOR_UNITS
    state = {"draft": 0, "review": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            if "【要改的这一拍】" in prompt:               # revise 按拍重写
                return {"chunks": [beat1_new], "usage": U(5, 10)}
            which = state["draft"]
            state["draft"] += 1
            return {"chunks": [beat1 if which == 0 else beat2], "usage": U(5, 10)}
        if "【待审正文】" in prompt:
            state["review"] += 1
            if state["review"] == 1:                        # 初审：1 条 blocking（rewrite 类）
                issue = {"type": "logic", "fix_kind": "rewrite", "quote": "甲沿着山道",
                         "issue": "节奏拖沓", "suggestion": "加快"}
                return json.dumps({"pass": False, "issues": [issue]}, ensure_ascii=False), U(3, 4)
            return REVIEW_PASS, U(3, 4)                     # 复审通过
        if "【被点名的片段】" in prompt:                     # deaify 定点：12 条全落地
            edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
            return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "你是小说记忆整理员" in prompt:                  # 记忆提取（初写/revise/deaify 后各一次）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    llm, _calls = make_llm(router)
    result = await run_tool(store, llm, Collector(), {"plot_id": "1-1"})
    # select 1×(1,2) + 逐拍 2×(5,10) + review 2×(3,4) + revise 按拍重写 1×(5,10)
    # + deaify 定点 1×(2,3) + 记忆提取 3×(2,3)
    assert result.usage is not None
    assert result.usage.prompt_tokens == 1 + 5 + 5 + 3 + 3 + 5 + 2 + 2 + 2 + 2
    assert result.usage.completion_tokens == 2 + 10 + 10 + 4 + 4 + 10 + 3 + 3 + 3 + 3


async def test_write_plot_observation_unchanged(store, make_llm):
    """三条提取路径（初写 / revise 后 / deaify 后）的观测文案逐字不变：
    notes 注「记忆提取失败」、chapter_done 的 memory_status 取值域
    （"extracted"/"failed"）、write_plot 首行与章行拼接一字不改。"""
    store.upsert_plots(1, [dict(PLOT), dict(PLOT, seq=2), dict(PLOT, seq=3)])
    write_chapters(store, [
        chapter(1, "题一", beats("一之起", "一之承"), plot_id="1-1"),
        chapter(2, "题二", beats("二之起", "二之承"), plot_id="1-2"),
        chapter(3, "题三", beats("三之起"), plot_id="1-3"),
    ])
    beat1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    beat1_new = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4
    beat3 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4 + FLAVOR_UNITS

    def _router(*, drafts, blocking_first=False, extract_fail_from=1):
        """drafts: 逐拍稿序列（每项是一拍的 chunk 列表）；blocking_first: 初审给 1 条
        blocking（进 revise 路径）；extract_fail_from: 第 n 次起的提取调用失败
        （1=初写起就失败）。"""
        state = {"draft": 0, "review": 0, "extract": 0}

        def router(body):
            prompt = body["messages"][-1]["content"]
            if body.get("stream"):
                if "【要改的这一拍】" in prompt:           # revise 按拍重写
                    return {"chunks": [beat1_new], "usage": U(5, 10)}
                which = state["draft"]
                state["draft"] += 1
                return {"chunks": drafts[which % len(drafts)], "usage": U(5, 10)}
            if "【待审正文】" in prompt:
                state["review"] += 1
                if blocking_first and state["review"] == 1:
                    issue = {"type": "logic", "fix_kind": "rewrite", "quote": "甲沿着山道",
                             "issue": "节奏拖沓", "suggestion": "加快"}
                    return (json.dumps({"pass": False, "issues": [issue]}, ensure_ascii=False),
                            U(3, 4))
                return REVIEW_PASS, U(3, 4)
            if "【被点名的片段】" in prompt:               # deaify 定点：12 条全落地
                edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
                return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
            if "【资源目录（可挑选的内容）】" in prompt:
                return SELECT_OK, U(1, 2)
            if "你是小说记忆整理员" in prompt:
                state["extract"] += 1
                if state["extract"] >= extract_fail_from:
                    return "不是 JSON", U(2, 3)          # 提取失败（扩额两轮仍坏 JSON）
                return EXTRACT_OK, U(2, 3)
            raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

        return router

    def _assert_obs(result, collector, seq, title, ref, memory_status):
        doc = store.read_chapter(seq)
        assert result.text.splitlines() == [
            f"write_plot：情节「开端」({ref}) 写作完成——成功 1 章",
            f"第{seq}章「{title}」：{count_words(doc.body)} 字，审核通过（记忆提取失败）",
        ]
        done = [p for k, p in collector.events if k == "chapter_done"]
        assert done[-1]["memory_status"] == memory_status

    # ① 初写路径：提取失败 → notes 注「记忆提取失败」、memory_status="failed"
    c1 = Collector()
    llm1, _ = make_llm(_router(drafts=[draft_chunks()], extract_fail_from=1))
    r1 = await run_tool(store, llm1, c1, {"plot_id": "1-1"})
    _assert_obs(r1, c1, 1, "题一", "1-1", "failed")

    # ② revise 路径：初写提取成功、revise 后重提取失败 → memory_status="extracted"
    c2 = Collector()
    llm2, _ = make_llm(_router(drafts=[[beat1], [beat2]], blocking_first=True,
                               extract_fail_from=2))
    r2 = await run_tool(store, llm2, c2, {"plot_id": "1-2"})
    _assert_obs(r2, c2, 2, "题二", "1-2", "extracted")

    # ③ deaify 路径：初写提取成功、deaify 后重提取失败 → memory_status="extracted"
    c3 = Collector()
    llm3, _ = make_llm(_router(drafts=[[beat3]], extract_fail_from=2))
    r3 = await run_tool(store, llm3, c3, {"plot_id": "1-3"})
    _assert_obs(r3, c3, 3, "题三", "1-3", "extracted")


# ------------------------------------------------------------------ 现实参照块选取（P5 T10）

def test_web_facts_block_selection(tmp_path):
    """research.md 条目 → top-3 选取；总 ≤1200 字；无条目 → 空串。"""
    from harness.projects import create_project
    from harness.store.project import ChapterDoc, ProjectStore
    from harness.tools.writing import _web_facts_block
    from harness.websearch import ResearchEntry, append_research, query_key_of

    info = create_project(tmp_path, "参照书")
    store = ProjectStore(info.path)
    doc = ChapterDoc(seq=1, meta={"seq": 1, "title": "第1章"}, body="")
    beats = [{"pov": "甲", "location": "山门", "event": "甲整顿内政屯田"}]

    # 空 research.md → 空串
    assert _web_facts_block(store, doc, beats) == ""

    # 检索是 trigram 词面（retrieval/bm25.py）——查询词须与本章拍事件共享 3-gram
    for i, q in enumerate(["内政 屯田 征兵 系统", "其他 条目 一", "其他 条目 二", "其他 条目 三"]):
        append_research(store.root, ResearchEntry(query=q, query_key=query_key_of(q),
                                                  kind="fact", conclusion=f"结论{i}",
                                                  sources=[], hit_count=1))
    block = _web_facts_block(store, doc, beats)
    assert block
    assert "内政 屯田 征兵 系统" in block        # 与本章最相关（共享 trigram）
    assert len(block) <= 1200
    assert block.count("· ") <= 3                # top-3
