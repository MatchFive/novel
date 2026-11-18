# 审计 LLM 复核（P5 Task 2）
"""review_chapter 语义钉死（seed check_service.py:110-139 解析/去重 + prompt「举证门槛」
的代码层回验）：

- task.consistency_audit 单次调用（路由 standard/0.2/4096）→ 解析 JSON 数组或
  ``{"issues": [...]}`` 包装；
- quote 回验：每条 item 的 quote 必须 locate_quote 命中 draft_text，编造的 quote
  丢弃（seed 只做 as_text 规整不校验，本地版加回验）；
- chapter 以传入 chapter_seq 覆盖（不信模型自报）；
- 同批 (type, 去空白 issue) 去重；
- 坏字段条目（quote 缺失 / issue 非 str / 非 dict）跳过不炸；
- usage 透传返回元组第二元素（响应未带 → None）；
- 失败 raise（R1 裁定）：LLM 调用失败 → LLMError、解析失败（非 JSON / 顶层形态
  非法）→ ValueError，消息中文无瞬时词；空数组 = 真无问题，返回 ([], usage)。
"""
from __future__ import annotations

import json

import pytest

from harness.audit.review import review_chapter
from harness.config import ProviderConfig
from harness.llm.client import CompletionUsage, LLMError
from harness.prompts.loader import PromptLoader

DRAFT = "甲拔刀，乙点火。夜风吹过山寨。"
REAL_QUOTE = "夜风吹过山寨"
FAKE_QUOTE = "这段文字正文里根本不存在"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本：_Resp 项为响应；Exception 项直接 raise。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(
            base_url="https://api.test", api_key="k",
            lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def loader(home):
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(home_dir=home)


def item(quote=REAL_QUOTE, type_="timeline", issue="时间线与铁律锚矛盾",
         suggestion="修订该章时间线", chapter=99):
    return {"chapter": chapter, "quote": quote, "type": type_,
            "issue": issue, "suggestion": suggestion}


def payload(items):
    return _Resp(json.dumps(items, ensure_ascii=False))


async def run(llm, tmp_path, seq=3):
    return await review_chapter(
        llm=llm, prompts=loader(tmp_path / "home"), chapter_seq=seq,
        draft_text=DRAFT, canonical_entries="铁律一", cast_cards="【甲】(主角)",
        character_memories="### 甲", knowledge_state="- 甲｜realm: 筑基",
        open_promises="- [foreshadow] 后山密道")


async def test_fabricated_quote_discarded(tmp_path):
    """quote 回验：一条真一条编造 → 只留真 quote 那条。"""
    llm = FakeLLM([payload([item(quote=REAL_QUOTE), item(quote=FAKE_QUOTE)])])
    findings, _usage = await run(llm, tmp_path)
    assert len(findings) == 1
    assert findings[0].quote == REAL_QUOTE


async def test_valid_quote_kept(tmp_path):
    """quote 逐字在正文 → 保留；chapter=传入 seq（模型自报 99 被覆盖）。"""
    llm = FakeLLM([payload([item()])])
    findings, _usage = await run(llm, tmp_path, seq=3)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "consistency_timeline"
    assert f.chapter == 3
    assert f.quote == REAL_QUOTE
    assert f.title == "时间线与铁律锚矛盾"[:40]
    assert f.detail == "timeline：时间线与铁律锚矛盾；建议：修订该章时间线"


async def test_issues_wrapper_parsed(tmp_path):
    """{"issues": [...]} 包装同样解析（seed check_service.py:119 口径）。"""
    resp = _Resp(json.dumps({"issues": [item()]}, ensure_ascii=False))
    llm = FakeLLM([resp])
    findings, _usage = await run(llm, tmp_path)
    assert len(findings) == 1
    assert findings[0].rule == "consistency_timeline"


async def test_dedup_same_type_issue(tmp_path):
    """同批同 type 同 issue（去空白后相同）两条 → 只留 1 条。"""
    pair = [item(issue="时间线 与铁律锚矛盾"), item(issue="时间线与铁律锚矛盾")]
    llm = FakeLLM([payload(pair)])
    findings, _usage = await run(llm, tmp_path)
    assert len(findings) == 1


async def test_bad_item_fields_skipped(tmp_path):
    """坏字段条目（quote 缺失 / issue 非 str / 非 dict）跳过不炸，好条目照留。"""
    bad = [item(),
           {"type": "timeline", "issue": "quote 缺失的条目"},
           {"quote": REAL_QUOTE, "type": "timeline", "issue": 123},
           "不是 dict 的条目"]
    llm = FakeLLM([payload(bad)])
    findings, _usage = await run(llm, tmp_path)
    assert len(findings) == 1
    assert findings[0].quote == REAL_QUOTE


async def test_usage_returned(tmp_path):
    """mock 响应带 usage → 返回元组第二元素即该 usage。"""
    usage = CompletionUsage(prompt_tokens=11, completion_tokens=22)
    llm = FakeLLM([_Resp(json.dumps([item()], ensure_ascii=False), usage=usage)])
    findings, got = await run(llm, tmp_path)
    assert len(findings) == 1
    assert got == usage  # complete_structured 跨尝试累计为等值新对象


async def test_empty_array_no_findings(tmp_path):
    """空数组（宁缺毋滥的合法产出）→ 真无问题：返回 ([], usage)，不 raise。"""
    llm = FakeLLM([payload([])])
    findings, usage = await run(llm, tmp_path)
    assert findings == []
    assert usage is None


async def test_llm_failure_raises(tmp_path):
    """LLM 调用失败 → raise LLMError（R1：T3 据此区分「真无问题」与「复核失败」）。"""
    llm = FakeLLM([LLMError("boom", transient=True, kind="timeout")])
    with pytest.raises(LLMError, match="一致性复核失败（调用失败）"):
        await run(llm, tmp_path)


async def test_parse_failure_raises(tmp_path):
    """响应两轮后仍非 JSON → raise ValueError（解析失败不得静默当无问题）。"""
    llm = FakeLLM([_Resp("不是 JSON"), _Resp("还是不是 JSON")])
    with pytest.raises(ValueError, match="一致性复核解析失败"):
        await run(llm, tmp_path)


async def test_bad_top_level_shape_raises(tmp_path):
    """顶层既非数组也非 {"issues": list} → raise ValueError。"""
    llm = FakeLLM([payload({"foo": 1})])
    with pytest.raises(ValueError, match="顶层既非 JSON 数组"):
        await run(llm, tmp_path)


async def test_prompt_rendered_and_routing(tmp_path):
    """提示词 = 逐字模板渲染六变量；调用按 WRITING_ROUTING 路由参数发出。"""
    llm = FakeLLM([payload([])])
    await run(llm, tmp_path)
    call = llm.calls[0]
    assert (call["model"], call["temperature"], call["max_tokens"]) \
        == ("m-std", 0.2, 4096)
    prompt = call["prompt"]
    assert "【铁律设定】铁律一" in prompt
    assert "【角色卡】【甲】(主角)" in prompt
    assert "【角色经历档案】### 甲" in prompt
    assert "【知识状态】- 甲｜realm: 筑基" in prompt
    assert "【未回收承诺】- [foreshadow] 后山密道" in prompt
    assert "【待审正文】" + DRAFT in prompt
