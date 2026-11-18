"""P3 验收六场景（LLM 全 mock：JSON-body / SSE 网关同 tests/tools/test_writing.py，
_sse_body 复用 tests/tools/test_base.py）。全部真实 ProjectStore + EventLog（emit 直绑
events.append）+ write_plot 工具级入口（不经 loop——分类测试 T8 已覆盖 loop 层）。

1. 全链三 Chapter：手工 beats（weight 1/3 摊分可手算）→ 字数与拍预算一致、正文合成、
   快照/事件（events.log 实文件）/观测全断言。
2. review 出一条 replace → 零额外 LLM 调用已就地修复、pass 落盘。
3. blocking 逻辑问题 → 按拍重写 → detect serious → 定点去味分数降 →
   chapter_revised 事件 + 快照 flavor 分 == 对终稿独立 detect 分。
4. 幂等重跑：done 章不重演——零 LLM 调用、零新增事件。
5. 断点：第 2 章流式失败暂停 → 修 mock 再跑 → 只补第 2 章（第 3 章无细纲跳过、零调用）。
6. 分数可复现：同一正文 detect 两次一致；快照分 == 落库正文重算分。
"""
import json
from pathlib import Path

import httpx
from tools.test_base import _sse_body

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.events import EventLog
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import count_words
from harness.tools.writing import _flavor_items, write_plot
from harness.writing.flavor import THRESHOLD, detect
from harness.writing.pure import beat_word_budgets

PLOT = {"seq": 1, "title": "开端", "summary": "概要一", "conflict": "冲突一",
        "resolution": "收束一", "status": "planned"}

SELECT_OK = json.dumps({"world_titles": [], "character_names": [],
                        "keywords": [], "need_web": []}, ensure_ascii=False)
REVIEW_PASS = json.dumps({"pass": True, "issues": []}, ensure_ascii=False)
EXTRACT_OK = json.dumps({"events": [], "character_events": [], "info_updates": [],
                         "promise_updates": [],
                         "state_updates": {"characters": [], "world": []}},
                        ensure_ascii=False)

FLAVOR_MIDDLES = ["明亮", "晃眼", "刺眼", "灼目", "白炽", "耀眼",
                  "晃荡", "雪亮", "眩目", "烁金", "煌煌", "烨烨"]
FLAVOR_UNITS = "".join(f"那么{m}，那么刺眼。" for m in FLAVOR_MIDDLES)


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def beat(event, weight=1):
    return {"pov": "甲", "location": "山道", "event": event, "weight": weight}


def chapter(seq, title, beat_list, target=3000, plot_id="1-1"):
    meta = {"seq": seq, "title": title, "objective": f"目标{seq}", "status": "planned",
            "beats": beat_list, "covers": f"覆盖{seq}", "time_span": "夜",
            "start_at": "山道｜夜", "end_at": "山道｜夜",
            "plot_id": plot_id, "target_words": target}
    return ChapterDoc(seq=seq, meta=meta, body="")


def build_store(tmp_path, docs, title="验收书"):
    info = create_project(tmp_path, title)
    store = ProjectStore(info.path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    store.upsert_plots(1, [dict(PLOT)])
    for doc in docs:
        store.write_chapter(doc)
    return store


def make_llm(router):
    """router(body) -> str | (content, usage) | {"chunks","usage"?} | Exception；
    stream 请求走 SSE（复用 test_base._sse_body），非流式走 JSON body。"""
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
            return httpx.Response(200, content=_sse_body(*chunks, usage=usage),
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


def draft_chunks(seed, n_chars=1400):
    """非严格周期的长正文（逐段变编号）：repeat_loop_cut 要求 ≥24 字严格循环单元，
    变编号拼接不会命中熔断。约 1400 字/拍，两拍 ≈2800 字 ≥ 0.85×3000 护栏。"""
    chunks, total, k = [], 0, 1
    while total < n_chars:
        c = f"第{k}段{seed}沿着山道继续往前走，天色一点点暗下来，远处有鸟叫。"
        chunks.append(c)
        total += len(c)
        k += 1
    return chunks


def good_router():
    """逐拍流式（【任务】锁在场）+ select + review 全过的默认路由。"""
    state = {"n": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【任务】" in prompt and "只写" in prompt   # 逐拍锁在场
            state["n"] += 1
            return {"chunks": draft_chunks(f"稿{state['n']}号"), "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "你是小说记忆整理员" in prompt:                      # task.memory_extract（P4 挂点）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    return router


async def run_write(store, llm, events, args=None):
    return await write_plot(store, llm, PromptLoader(home_dir=store.root),
                            make_settings(), events.append, args or {"plot_id": "1-1"})


def read_events(store):
    return [json.loads(line) for line in
            (store.root / ".harness" / "events.log").read_text(encoding="utf-8")
            .splitlines()]


def read_snapshot(store, seq):
    return json.loads((store.root / ".harness" / "review" / f"{seq}.json")
                      .read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 场景 1：全链三 Chapter

async def test_full_chain_three_chapters(tmp_path):
    """3 章（每章 2 拍、weight 1/3）→ 拍预算手算 [750, 2250]、正文逐拍合成、
    字数 == count_words、快照/事件（events.log 实文件）/观测全断言。"""
    docs = []
    for seq, title in ((1, "题一"), (2, "题二"), (3, "题三")):
        docs.append(chapter(seq, title, [beat(f"第{seq}章第1拍", weight=1),
                                         beat(f"第{seq}章第2拍", weight=3)]))
    store = build_store(tmp_path, docs)
    events = EventLog(store.root)
    llm, calls = make_llm(good_router())
    result = await run_write(store, llm, events)

    # 拍预算：weight 1/3 摊 3000 → max(300, round(3000×1/4))=750 / max(300, 2250)=2250
    assert beat_word_budgets(3000, docs[0].meta["beats"]) == [750, 2250]

    assert "写作完成" in result.text and "成功 3 章" in result.text
    for seq, title in ((1, "题一"), (2, "题二"), (3, "题三")):
        assert f"第{seq}章「{title}」" in result.text       # 观测逐章行
    # 字数与拍预算一致：正文 == 两拍流式正文拼接（无空白边），word_count 同口径
    docs_out = [store.read_chapter(seq) for seq in (1, 2, 3)]
    expected_lens = []
    stream_calls = [b for b in calls if b.get("stream")]
    for ch in range(3):
        beat1 = "".join(draft_chunks(f"稿{ch * 2 + 1}号"))
        beat2 = "".join(draft_chunks(f"稿{ch * 2 + 2}号"))
        assert docs_out[ch].body == beat1 + beat2          # 逐拍合成、normalize_edges 无损耗
        expected_lens.append(count_words(beat1 + beat2))
    for ch, doc in enumerate(docs_out):
        assert doc.meta["status"] == "done"
        assert doc.meta["word_count"] == count_words(doc.body) == expected_lens[ch]
        assert 2550 <= doc.meta["word_count"] <= 4800      # 落在 0.85×~1.6× 目标护栏内
        snap = read_snapshot(store, ch + 1)
        assert snap["pass_review"] is True and snap["issues"] == []
        assert snap["revise_count"] == 0 and snap["deaified"] is False
        assert isinstance(snap["flavor_score"], (int, float))
        assert isinstance(snap["flavor_items"], list) and snap["at"]
    # 调用序：逐章 select 1 + 逐拍 stream 2 + review 1 + 记忆提取 1 → 流式 6 + 非流式 9
    assert len(stream_calls) == 6 and len(calls) - len(stream_calls) == 9
    assert result.usage is not None

    # 事件（events.log 实文件）：draft_progress 之外 chapter_start/done 交替、seq 递增
    evs = read_events(store)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "chapter_start" and kinds[-1] == "chapter_done"
    lifecycle = [e for e in evs if e["kind"] != "draft_progress"]
    assert [e["kind"] for e in lifecycle] == ["chapter_start", "chapter_done"] * 3
    assert [e["payload"]["seq"] for e in lifecycle] == [1, 1, 2, 2, 3, 3]
    for e in lifecycle:
        if e["kind"] == "chapter_start":
            assert e["payload"]["target_words"] == 3000
        else:
            assert e["payload"]["pass_review"] is True
            assert e["payload"]["memory_status"] == "extracted"
            assert e["payload"]["words"] == docs_out[e["payload"]["seq"] - 1].meta["word_count"]
    progress = [e for e in evs if e["kind"] == "draft_progress"]
    assert progress and all(set(e["payload"]) == {"seq", "beat", "chars", "phase"}
                            for e in progress)
    assert all(e["payload"]["phase"] == "draft" and e["payload"]["chars"] > 0
               for e in progress)


# ---------------------------------------------------------------- 场景 2：replace 零 LLM 修复

async def test_replace_fix_zero_llm(tmp_path):
    """review 出一条 replace（引文可定位）→ apply_replace_issues 就地置换、零额外
    LLM 调用、pass 落盘；快照 issues 空、revise_count 0、全文只有 5 次调用。"""
    store = build_store(tmp_path, [chapter(1, "题一", [beat("一之起"), beat("一之承")])])
    beat1_text = "".join(draft_chunks("修1号"))
    quote = beat1_text[:30]                                # 取可精确定位的一段引文
    replacement = "甲改走林间小路，脚步明显快了起来。"
    issue = {"type": "logic", "fix_kind": "replace", "quote": quote,
             "replacement": replacement, "issue": "称呼与走向矛盾", "suggestion": "统一替换"}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            state = router_state
            state["n"] += 1
            return {"chunks": draft_chunks(f"修{state['n']}号"), "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "【待审正文】" in prompt:
            return json.dumps({"pass": False, "issues": [issue]}, ensure_ascii=False), U(3, 4)
        if "你是小说记忆整理员" in prompt:                      # 记忆提取（P4 挂点）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    router_state = {"n": 0}
    events = EventLog(store.root)
    llm, calls = make_llm(router)
    result = await run_write(store, llm, events)

    doc = store.read_chapter(1)
    assert doc.meta["status"] == "done"
    assert quote not in doc.body and replacement in doc.body   # 定点置换生效
    snap = read_snapshot(store, 1)
    assert snap["pass_review"] is True and snap["issues"] == []
    assert snap["revise_count"] == 0 and snap["deaified"] is False
    assert "审核通过" in result.text and "未通过" not in result.text
    # 零额外 LLM：select 1 + stream 2 + review 1 + 记忆提取 1 = 5；无复审（review 锚点
    # 只出现 1 次）、无按拍重写（stream 全在 review 之前）
    assert len(calls) == 5
    n_stream = sum(1 for b in calls if b.get("stream"))
    assert n_stream == 2
    review_idx = next(i for i, b in enumerate(calls)
                      if not b.get("stream")
                      and "【待审正文】" in b["messages"][-1]["content"])
    assert review_idx == len(calls) - 2                   # review 后是记忆提取（无复审/重写）
    evs = [e for e in read_events(store) if e["kind"] != "draft_progress"]
    assert [e["kind"] for e in evs] == ["chapter_start", "chapter_done"]
    assert evs[-1]["payload"]["pass_review"] is True


# ---------------------------------------------------------------- 场景 3：按拍重写 + 定点去味

async def test_blocking_beat_rewrite_then_deaify(tmp_path):
    """blocking（rewrite）→ 按拍重写一轮收敛 → detect serious → 定点去味分数降 →
    chapter_revised 事件 + 快照 flavor 分 == 终稿独立 detect 分（去味前旧分不得进快照）。"""
    store = build_store(tmp_path, [chapter(1, "题一", [beat("一之起"), beat("一之承")])])
    beat1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4 + FLAVOR_UNITS
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    beat1_new = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4 + FLAVOR_UNITS
    revised_body = beat1_new + beat2                        # normalize_edges 无损耗的预期修订稿
    state = {"draft": 0, "review": 0, "edits": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            if "【要改的这一拍】" in prompt:               # 按拍重写（logic 问题定位到第 1 拍）
                return {"chunks": [beat1_new], "usage": U(5, 10)}
            assert "【任务】" in prompt and "只写" in prompt
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
            state["edits"] += 1
            edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
            return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
        if "你是小说记忆整理员" in prompt:                      # 记忆提取（初写/revise/deaify 后各一次）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    events = EventLog(store.root)
    llm, calls = make_llm(router)
    result = await run_write(store, llm, events)

    doc = store.read_chapter(1)
    assert doc.meta["status"] == "done"
    assert doc.body.count("天光大亮。") == 12              # 定点去味落地
    assert "beat_spans" not in doc.meta                   # 去味后区间作废
    # 定点去味分数降：修订稿仍 serious，去味后低于阈值且严格低于修订稿
    pre = detect(revised_body)
    final_report = detect(doc.body)                       # 对终稿独立重算
    assert pre.score >= THRESHOLD and pre.serious
    assert final_report.score < pre.score and final_report.score < THRESHOLD
    snap = read_snapshot(store, 1)
    assert snap["deaified"] is True                       # 快照声称已去味
    assert snap["revise_count"] == 1 and snap["pass_review"] is True
    assert snap["flavor_score"] == final_report.score     # 快照分 == 终稿独立 detect 分
    assert snap["flavor_items"] == _flavor_items(final_report)
    assert "审核通过" in result.text and "未通过" not in result.text   # verdict 取复审后结论
    # 事件：chapter_revised 落 events.log，取复审后结论
    evs = read_events(store)
    revised = [e for e in evs if e["kind"] == "chapter_revised"]
    assert len(revised) == 1
    assert revised[0]["payload"]["pass_review"] is True
    assert revised[0]["payload"]["revise_count"] == 1
    kinds = [e["kind"] for e in evs if e["kind"] != "draft_progress"]
    assert kinds == ["chapter_start", "chapter_done", "chapter_revised"]
    phases = [e["payload"]["phase"] for e in evs if e["kind"] == "draft_progress"]
    assert "revising" in phases and "deaify" in phases
    # 调用结构：草稿 stream×2 + 按拍重写 stream×1 + 定点去味零 stream 兜底
    assert sum(1 for b in calls if b.get("stream")) == 3
    assert sum(1 for b in calls
               if not b.get("stream") and "【被点名的片段】" in b["messages"][-1]["content"]) \
        == state["edits"] >= 1


# ---------------------------------------------------------------- 场景 4：幂等重跑

async def test_idempotent_rerun_skips_done(tmp_path):
    """全量写完后重跑同 plot_id：done 章不重演——零 LLM 调用、events.log 零新增。"""
    store = build_store(tmp_path, [chapter(1, "题一", [beat("一之起"), beat("一之承")])])
    events = EventLog(store.root)
    llm1, _calls1 = make_llm(good_router())
    result1 = await run_write(store, llm1, events)
    assert "成功 1 章" in result1.text
    events_before = (store.root / ".harness" / "events.log").read_bytes()

    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("幂等重跑不应触发任何 LLM 调用")

    llm2, calls2 = make_llm(router)
    result2 = await run_write(store, llm2, EventLog(store.root))
    assert "write_plot 失败（无产出）" in result2.text
    assert "status=done" in result2.text
    assert calls2 == []                                    # 零 LLM 调用
    assert (store.root / ".harness" / "events.log").read_bytes() == events_before


# ---------------------------------------------------------------- 场景 5：断点续跑（只补失败的章）

async def test_resume_after_failure(tmp_path):
    """第 2 章流式调用失败 → 该章不落盘、后续章不动；修复 mock 再跑 → 只补第 2 章
    （第 1 章零调用、第 3 章无细纲跳过零调用）。"""
    store = build_store(tmp_path, [
        chapter(1, "题一", [beat("第1章第1拍"), beat("第1章第2拍")]),
        chapter(2, "题二", [beat("第2章第1拍"), beat("第2章第2拍")]),
        chapter(3, "题三", []),                            # 无细纲：两轮都跳过、零 LLM
    ])
    events = EventLog(store.root)
    state = {"stream": 0}

    def failing_router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            state["stream"] += 1
            if state["stream"] == 3:                       # 第 2 章第 1 拍
                return httpx.ConnectError("boom")
            return {"chunks": draft_chunks(f"断{state['stream']}号"), "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "你是小说记忆整理员" in prompt:                      # task.memory_extract（P4 挂点）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    llm1, _calls1 = make_llm(failing_router)
    result1 = await run_write(store, llm1, events)
    one = store.read_chapter(1)
    assert one.meta["status"] == "done" and one.body       # 第 1 章完好
    two = store.read_chapter(2)
    assert two.meta["status"] == "planned" and two.body == ""   # 失败的章不落盘
    assert not (store.root / ".harness" / "review" / "2.json").exists()
    three = store.read_chapter(3)
    assert three.meta["status"] == "planned" and three.body == ""  # 后续章不动
    assert "第2章：未成功（调用失败）" in result1.text
    assert "成功 1 章" in result1.text and "失败 1 章" in result1.text
    lifecycle1 = [e for e in read_events(store) if e["kind"] != "draft_progress"]
    assert [(e["kind"], e["payload"]["seq"]) for e in lifecycle1] \
        == [("chapter_start", 1), ("chapter_done", 1), ("chapter_start", 2)]

    # 修复 mock 再跑：只补第 2 章
    llm2, calls2 = make_llm(good_router())
    result2 = await run_write(store, llm2, EventLog(store.root))
    assert "成功 1 章" in result2.text
    two = store.read_chapter(2)
    assert two.meta["status"] == "done" and two.body
    assert two.meta["word_count"] == count_words(two.body)
    assert read_snapshot(store, 2)["pass_review"] is True
    three = store.read_chapter(3)                          # 无细纲：跳过、原样
    assert three.meta["status"] == "planned" and three.body == ""
    assert "第3章：无细纲，跳过" in result2.text and "跳过 1 章" in result2.text
    # 只补第 2 章的调用：select 1 + stream 2 + review 1 + 记忆提取 1 = 5，全部锚定第 2 章细纲
    assert len(calls2) == 5
    assert sum(1 for b in calls2 if b.get("stream")) == 2
    prompts2 = [b["messages"][-1]["content"] for b in calls2]
    assert all("第1章第1拍" not in p and "题一" not in p for p in prompts2)
    assert any("第2章第1拍" in p for p in prompts2)
    lifecycle2 = [e for e in read_events(store) if e["kind"] != "draft_progress"]
    done_seqs = [e["payload"]["seq"] for e in lifecycle2 if e["kind"] == "chapter_done"]
    assert done_seqs == [1, 2]                             # 第 1 章不重演、第 2 章补齐


# ---------------------------------------------------------------- 场景 6：分数可复现

async def test_flavor_reproducible(tmp_path):
    """同一正文 detect 两次：分数与条目逐字一致（改前改后可复现）；且快照 flavor 分 ==
    落库正文独立重算分（同一份正文，落盘前后判分不漂移）。审核通过但 detect serious
    的章：deaify 在 pass 路径同样执行（终审 I-1）——快照 deaified=True、分数为终稿分、
    word_count 为去味后值。"""
    store = build_store(tmp_path, [chapter(1, "题一", [beat("一之起"), beat("一之承")])])
    beat1 = "甲深吸一口气，沿着山道往前走，天色一点点暗下来。" * 4 + FLAVOR_UNITS
    beat2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
    CLEAN_REPL = ["天光大亮。", "晨光透林。", "鸟叫又起。", "风过谷口。", "云影移山。",
                  "溪水涨了一寸。", "火光跳了跳。", "远处狗吠。", "尘土落定。", "日头偏西。",
                  "草叶响动。", "雾气散开。", "脚步放慢。"]

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            state = router_state
            state["n"] += 1
            return {"chunks": [beat1 if state["n"] == 1 else beat2], "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "【被点名的片段】" in prompt:                     # pass 路径去味：13 个定点目标全落地
            edits = [{"index": i, "replacement": CLEAN_REPL[i - 1]}
                     for i in range(1, 14)]
            return json.dumps({"edits": edits}, ensure_ascii=False), U(2, 3)
        if "你是小说记忆整理员" in prompt:                      # 记忆提取（初写 + deaify 后各一次）
            return EXTRACT_OK, U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    router_state = {"n": 0}
    events = EventLog(store.root)
    llm, _calls = make_llm(router)
    result = await run_write(store, llm, events)
    assert "审核通过" in result.text

    body = store.read_chapter(1).body
    assert "那么" not in body                              # 12 处二元并置已被定点去味
    r1, r2 = detect(body), detect(body)                    # 同一正文两次 detect
    assert r1.score == r2.score
    assert _flavor_items(r1) == _flavor_items(r2)
    snap = read_snapshot(store, 1)
    final = detect(body)                                   # 对落库正文独立重算
    assert snap["flavor_score"] == final.score
    assert snap["flavor_items"] == _flavor_items(final)
    # 审核通过但超标：去味照样执行（I-1），快照/字数都是去味后的值
    assert snap["deaified"] is True
    assert store.read_chapter(1).meta["word_count"] == count_words(body)
