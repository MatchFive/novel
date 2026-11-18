"""Task 7：三级修正收敛循环 + deaify 两阶段收口测试。

MockTransport 网关同 tests/tools/test_writing.py（_sse_body 复用 tests/tools/test_base.py）：
按模板锚点路由——【要改的这一拍】=task.draft_rewrite_beat（stream）、【待修正正文】=
task.draft_revise 整章（stream）、【待审正文】=task.draft_review 复审（非流式）、
【被点名的片段】=task.draft_deaify_edits（非流式）、【待改正文】=task.draft_deaify
兜底（stream）。路由不到的请求一律 AssertionError，钉死「该路径不应有 LLM 调用」。
"""
import json
from pathlib import Path

import httpx
import pytest
from tools.test_base import _sse_body

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.base import UsageMeter
from harness.writing import revise
from harness.writing.flavor import detect
from harness.writing.pure import MAX_REVISE

B1 = "甲沿着山道往前走，天色一点点暗下来，远处传来鸟叫。" * 4
B2 = "乙在河边生起火，把湿透的衣角凑近火苗。" * 4
BODY = B1 + B2
SPANS = [[0, len(B1)], [len(B1), len(BODY)]]
B1_NEW = "丙从林间转出来，刀鞘敲在树干上，惊起一片宿鸟。" * 4

MATERIALS = {"world_entries": "", "cast_cards": "", "pov_memories": "",
             "knowledge_state": "", "outline": "细纲：甲过山道；乙到河边生火。"}

# 去味正文：FILLER 干净；12 个「那么XX，那么刺眼。」变体 = 二元并置 ×12（可改写命中；
# targets() 按 (rule, quote) 去重，故引文必须两两不同），分数 ≫ THRESHOLD(6) → serious。
FILLER = "山道崎岖，他一步一步往上走，火把照亮脚边的碎石。" * 20
#: 一句**确定性检测完全不认**的话（用来说明「审核判出 ai_flavor」这个独立信号）
QUOTE = "他的眼眶忽然有些发热，视线模糊了一瞬。"
_MIDDLES = ["明亮", "晃眼", "刺眼", "灼目", "白炽", "耀眼",
            "晃荡", "雪亮", "眩目", "烁金", "煌煌", "烨烨"]
UNITS = [f"那么{m}，那么刺眼。" for m in _MIDDLES]
FLAVOR_BODY = FILLER + "".join(UNITS)


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "修正书")
    return ProjectStore(info.path)


@pytest.fixture
def make_llm():
    """router(body) -> str | (content, usage) | {"chunks", ...} | Exception（同 test_writing）。"""
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

    return factory


def doc_of(body):
    return ChapterDoc(seq=1, meta={"seq": 1, "title": "题", "status": "done",
                                    "word_count": 100}, body=body)


def rw_issue(quote, issue="节奏拖沓", suggestion="加快"):
    return {"type": "logic", "fix_kind": "rewrite", "quote": quote,
            "issue": issue, "suggestion": suggestion}


def review_json(issues):
    return json.dumps({"pass": not issues, "issues": issues}, ensure_ascii=False)


async def run_revise(store, llm, *, blocking, body=BODY, spans=SPANS, issues=None):
    return await revise.revise_until_converged(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        settings=make_settings(), emit=lambda k, p: None, doc=doc_of(body),
        materials=dict(MATERIALS), body=body, spans=spans, blocking=blocking,
        issues=issues if issues is not None else list(blocking), target_words=150)


async def run_deaify(store, llm, body, doc=None):
    doc = doc or doc_of(body)
    return await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(body))


# ---------------------------------------------------------------- 修正收敛循环

async def test_replace_issue_already_applied_at_review(store, make_llm):
    """review 阶段已消费 replace 定点置换：revise 内再过一遍 apply_replace_issues 幂等无害，
    定位不到的 replace 引文原样剩给按拍重写；rewrite 意见正常驱动按拍重写。"""
    blocking = [{"type": "logic", "fix_kind": "replace", "quote": "旧称呼甲",
                 "replacement": "丙", "issue": "称呼错", "suggestion": "统一替换"},
                rw_issue("甲沿着山道")]

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【要改的这一拍】" in prompt      # 只剩 rewrite 意见进按拍重写
            return {"chunks": [B1_NEW]}
        if "【待审正文】" in prompt:
            return review_json([])
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    new_body, new_spans, count, re_blocking, re_issues = await run_revise(
        store, llm, blocking=blocking)
    assert new_body == B1_NEW + B2                 # 替换引文未污染正文
    assert count == 1 and re_blocking == [] and re_issues == []
    assert new_spans[1][0] == len(B1_NEW)
    assert sum(1 for b in calls if b.get("stream")) == 1   # replace 零 LLM、无多余调用


async def test_beat_rewrite_splices_and_keeps_spans(store, make_llm):
    """可定位 issue → 只换该拍：其它拍逐字不动、spans 平移后仍逐字覆盖全文。"""
    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【要改的这一拍】" in prompt
            return {"chunks": [B1_NEW]}
        if "【待审正文】" in prompt:
            return review_json([])
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, _calls = make_llm(router)
    new_body, new_spans, count, re_blocking, _ = await run_revise(
        store, llm, blocking=[rw_issue("甲沿着山道")])
    assert count == 1 and re_blocking == []
    assert new_body == B1_NEW + B2                 # 第 2 拍逐字不动
    assert new_spans == [[0, len(B1_NEW)],
                         [len(B1_NEW), len(B1_NEW) + len(B2)]]
    assert new_body[new_spans[1][0]:new_spans[1][1]] == B2
    assert new_spans[-1][1] == len(new_body)       # spans 仍覆盖全文


async def test_beat_rewrite_too_short_keeps_original(store, make_llm):
    """<50% 守卫：改写稿过短视为截断残稿，保留原拍；复审同数 → 收敛守卫停。"""
    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            return {"chunks": ["短。"]}
        if "【待审正文】" in prompt:
            return review_json([rw_issue("甲沿着山道")])
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, _calls = make_llm(router)
    new_body, new_spans, count, re_blocking, _ = await run_revise(
        store, llm, blocking=[rw_issue("甲沿着山道")])
    assert new_body == BODY and new_spans == SPANS   # 原拍保留
    assert count == 1 and len(re_blocking) == 1      # 如实上报剩余


async def test_convergence_guard_stops_when_not_strictly_decreasing(store, make_llm):
    """复审后 blocking 计数未严格减少（2 → 2）→ 立即停，revise_count < MAX_REVISE。"""
    two = [rw_issue("甲沿着山道", issue="问题一"), rw_issue("甲沿着山道", issue="问题二")]

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            return {"chunks": [B1_NEW]}
        if "【待审正文】" in prompt:
            return review_json(two)                # 两轮同数
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    new_body, _spans, count, re_blocking, _ = await run_revise(store, llm, blocking=two)
    assert count == 1 and count < MAX_REVISE
    assert len(re_blocking) == 2
    assert sum(1 for b in calls if b.get("stream")) == 1     # 只重写了一轮
    # 计数未严格减少 ⇒ 该轮结果被弃用（seed best 跟踪：只收「问题更少」的版本）
    assert new_body == BODY and re_blocking == two


async def test_outline_issue_triggers_whole_and_truncation_guard(store, make_llm):
    """spans=None + outline（结构性）→ 整章 draft_revise；<70% 截断防护 → 保留原版、spans=None。"""
    outline = {"type": "outline", "fix_kind": "rewrite", "quote": "",
               "issue": "整章与细纲脱节", "suggestion": "按细纲重写"}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【待修正正文】" in prompt         # 整章重写锚点（非按拍）
            return {"chunks": ["太短。"]}
        if "【待审正文】" in prompt:
            return review_json([outline])
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    new_body, new_spans, count, re_blocking, _ = await run_revise(
        store, llm, blocking=[outline], spans=None)
    assert new_body == BODY and new_spans is None    # <70% 保留原版
    assert count == 1 and len(re_blocking) == 1
    assert sum(1 for b in calls if b.get("stream")) == 1


async def test_revise_round_llm_failure_keeps_original(store, make_llm):
    """revise 内 LLM 调用失败 → 该轮放弃：保留本轮前正文、issues 如实返回、不 raise。"""
    def router(body):
        if body.get("stream"):
            return httpx.ConnectError("boom")
        raise AssertionError(f"不该走到非流式：{body['messages'][-1]['content'][:60]}…")

    llm, _calls = make_llm(router)
    blocking = [rw_issue("甲沿着山道")]
    new_body, new_spans, count, re_blocking, _ = await run_revise(
        store, llm, blocking=blocking)
    assert new_body == BODY and new_spans == SPANS
    assert count == 0                                # 失败轮不计数
    assert re_blocking == blocking                   # 台账如实


# ---------------------------------------------------------------- deaify 两阶段

async def test_deaify_targeted_drops_overlong_and_keeps_better(store, make_llm):
    """定点轮：>1.8×+40 的过长 replacement 被丢弃；逐轮 detect 择优——分数反升的回滚。"""
    assert detect(FLAVOR_BODY).serious
    overlong = "晃" * 100
    edits_calls = {"n": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            raise AssertionError("分数已收敛，不应走整章兜底")
        if "【被点名的片段】" in prompt:
            edits_calls["n"] += 1
            if edits_calls["n"] == 1:
                edits = ([{"index": i, "replacement": "天光大亮。"} for i in range(1, 12)]
                         + [{"index": 12, "replacement": overlong}])
            else:  # 第 2 轮：把仅剩 1 处改出 2 处「那么…那么…」→ 分数反升
                edits = [{"index": 1,
                          "replacement": "那么晃，那么白煞煞，那么刺眼，那么晃眼。"}]
            return json.dumps({"edits": edits}, ensure_ascii=False)
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    doc = doc_of(FLAVOR_BODY)
    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(FLAVOR_BODY))
    assert changed is True
    assert overlong not in doc.body                  # 过长改写被丢弃
    assert doc.body.count("天光大亮。") == 11       # 11 条落地
    assert doc.body.count(UNITS[11]) == 1            # 第 12 处原样保留（回滚轮未生效）
    assert edits_calls["n"] == 2                     # 择优复检跑了第 2 轮
    assert sum(1 for b in calls if b.get("stream")) == 0


async def test_deaify_whole_fallback_and_worse_score_rejected(store, make_llm):
    """定点零落地（edits 全被丢弃）→ 整章兜底；兜底稿分数反升 → 拒绝，doc.body 不动。"""
    worse = "不是结束，而是开始。" * 30               # 30 处否定对偶，分数远超原文

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【待改正文】" in prompt
            return {"chunks": [worse]}
        if "【被点名的片段】" in prompt:
            return json.dumps({"edits": [{"index": 1, "replacement": "晃" * 100}]},
                              ensure_ascii=False)     # 过长全丢 → 零落地
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    doc = doc_of(FLAVOR_BODY)
    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(FLAVOR_BODY))
    assert changed is False and doc.body == FLAVOR_BODY
    assert sum(1 for b in calls if b.get("stream")) == 1   # 兜底跑了但被择优拒绝


async def test_deaify_whole_fallback_accepted_and_spans_dropped(store, make_llm):
    """兜底稿更优 → 采用：原地改 doc.body、逐拍区间作废（持久化归 writing.py）。"""
    clean = FILLER + "天光大亮。"

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【待改正文】" in prompt
            return {"chunks": [clean]}
        if "【被点名的片段】" in prompt:
            return json.dumps({"edits": []}, ensure_ascii=False)
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, _calls = make_llm(router)
    doc = doc_of(FLAVOR_BODY)
    doc.meta["beat_spans"] = {"sha": "x", "spans": [[0, 10]]}
    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(FLAVOR_BODY))
    assert changed is True and doc.body == clean
    assert "beat_spans" not in doc.meta


async def test_deaify_review_ai_flavor_triggers_without_serious(store, make_llm):
    """**L1**：``report.serious=False`` **但审核判出 ai_flavor** → **仍然触发去味**。

    设计 §1.4 的触发是**双信号**：「detect().serious（分数≥6/千字 且 ≥1 条可改写命中）
    **或** 审核判出 ai_flavor」。P3 本地只用了前者，理由（「review 的 ai_flavor 意见在
    调用点数据流不可得，双信号随 P4 恢复」）**已随 P4 落地而失效**——审核意见带 quote，
    且就在调用点同函数内（独立审核 L1）。
    """
    def router(body):
        prompt = body["messages"][-1]["content"]
        if "【被点名的片段】" in prompt:
            # 注意：本文件**没有** U()（它在 tests/tools/test_writing.py）→ 别用
            return json.dumps({"edits": [{"index": 1, "original": QUOTE,
                                          "replacement": "他的眼眶忽然觉得发热，视线模糊了一瞬。"}]},
                              ensure_ascii=False)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    # 正文 = 干净底稿 + 一句**确定性检测完全不认**的话（detect 的命中全是「只报不改」类）
    # → 分数 3.20 < 阈值 6.0 → **不 serious**；只有审核的 ai_flavor 这一个信号。
    # （引文必须是 detect 认不出的那句——否则「并入定点名单」这条路径就测不出来：
    #  detect 命中它时，并入与否都能改成功。）
    body = FILLER + QUOTE

    llm, calls = make_llm(router)
    doc = doc_of(body)
    report = detect(body)
    assert report.serious is False          # 确定性检测：没超标
    assert not [h for h in report.hits if QUOTE in h.quote]   # detect 不认这句

    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=report,
        flavor_quotes=[QUOTE])

    assert changed is True                  # **触发了**（双信号的第二个）
    assert calls                            # 有 LLM 调用（审核引文被并入定点名单）
    assert "他的眼眶忽然觉得发热，视线模糊了一瞬。" in doc.body
    assert QUOTE not in doc.body


async def test_deaify_review_quotes_merged_into_targets(store, make_llm):
    """**L1**：审核判出的 ai_flavor 引文**并入定点名单**（不然第二个信号触发了却没东西可改）。"""
    from harness.writing.flavor import FlavorHit
    from harness.writing.revise import _merge_review_quotes

    text = "前段。审核点名的句子。后段。"
    base = [FlavorHit(rule="某套路", weight=1.0, quote="前段。",
                      analysis="a", fix="b", score_only=False, start=0, end=3)]
    merged = _merge_review_quotes(base, ["审核点名的句子。"], text)

    assert len(merged) == 2
    assert merged[1].quote == "审核点名的句子。"
    assert merged[1].start == text.index("审核点名的句子。")
    # 定位不到 → 跳过（宁可不改）
    assert len(_merge_review_quotes(base, ["正文里没有的句子"], text)) == 1


async def test_deaify_not_serious_returns_false_without_llm(store, make_llm):
    """report.serious=False → 不触发任何 LLM 调用，原样返回 False。"""
    def router(body):  # pragma: no cover - 不该被调到
        raise AssertionError("不超标时不应有 LLM 调用")

    llm, calls = make_llm(router)
    doc = doc_of(FILLER)
    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(FILLER))
    assert changed is False and doc.body == FILLER and calls == []


# ---------------------------------------------------------------- usage 入账（P5 Task 4）

async def test_revise_usage_accumulated(store, make_llm):
    """revise 的 LLM 子调用 usage 记进 UsageMeter（按拍重写 + 复审之和）。"""
    meter = UsageMeter()

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            return {"chunks": [B1_NEW],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 10}}
        if "【待审正文】" in prompt:
            return review_json([]), {"prompt_tokens": 3, "completion_tokens": 4}
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, _calls = make_llm(router)
    new_body, _spans, count, re_blocking, _ = await revise.revise_until_converged(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        settings=make_settings(), emit=lambda k, p: None, doc=doc_of(BODY),
        materials=dict(MATERIALS), body=BODY, spans=SPANS,
        blocking=[rw_issue("甲沿着山道")], issues=[rw_issue("甲沿着山道")],
        target_words=150, meter=meter)
    assert count == 1 and re_blocking == []
    assert new_body == B1_NEW + B2                 # 第 2 拍逐字不动
    assert meter.total == CompletionUsage(prompt_tokens=8, completion_tokens=14)


async def test_revise_without_meter_behavior_unchanged(store, make_llm):
    """meter=None（默认）→ 与既有行为逐字相同：不记账、不改返回形状（5 元组）。"""
    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            return {"chunks": [B1_NEW],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 10}}
        if "【待审正文】" in prompt:
            return review_json([]), {"prompt_tokens": 3, "completion_tokens": 4}
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, _calls = make_llm(router)
    out = await revise.revise_until_converged(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        settings=make_settings(), emit=lambda k, p: None, doc=doc_of(BODY),
        materials=dict(MATERIALS), body=BODY, spans=SPANS,
        blocking=[rw_issue("甲沿着山道")], issues=[rw_issue("甲沿着山道")],
        target_words=150)
    assert len(out) == 5                                  # 返回形状不变（5 元组）


async def test_deaify_usage_accumulated(store, make_llm):
    """deaify 的 LLM 子调用 usage 记进 UsageMeter（定点改写那次）。"""
    meter = UsageMeter()

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            raise AssertionError("定点落地后不应走整章兜底")
        if "【被点名的片段】" in prompt:
            edits = [{"index": i, "replacement": "天光大亮。"} for i in range(1, 13)]
            return (json.dumps({"edits": edits}, ensure_ascii=False),
                    {"prompt_tokens": 2, "completion_tokens": 3})
        raise AssertionError(f"路由不到：{prompt[:60]}…")

    llm, calls = make_llm(router)
    doc = doc_of(FLAVOR_BODY)
    changed = await revise.deaify_if_needed(
        store=store, llm=llm, prompts=PromptLoader(home_dir=store.root),
        emit=lambda k, p: None, doc=doc, report=detect(FLAVOR_BODY), meter=meter)
    assert changed is True
    assert sum(1 for b in calls if b.get("stream")) == 0   # 只跑定点一轮
    assert meter.total == CompletionUsage(prompt_tokens=2, completion_tokens=3)
