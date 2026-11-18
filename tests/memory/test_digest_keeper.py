# DigestKeeper：卷级前情提要（P4 Task 6）
"""digest=投影非事实源（设计 §2.9）语义钉死：

- maintain：情节定稿后增量维护——读本情节各章记忆 events → 每章一段
  （``### 第N章《title》`` + 事件行）追加进 ``memory/digests/vol-<NN>.md`` 的
  ``## 情节 <seq>. <title>`` 节；**先清后建**：同情节节整节替换（幂等重跑无重复）；
- 单卷摘要超 DIGEST_LIMIT（2000 字）时最早情节节（不含本次维护的新节）交
  task.digest_compress（路由 lite/0.3/2048）压缩；压缩失败降级=只追加不压缩
  （宁可冗长不可丢，不 raise）；
- rebuild_async：整卷先清后建（可随时整卷重建）；
- read：digest 全文，无则 ""；digest 不进检索语料（T3 已排除 digests/）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage, LLMError
from harness.memory.digest import DIGEST_LIMIT, DigestKeeper
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader


class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage = CompletionUsage(prompt_tokens=1, completion_tokens=1)


class FakeLLM:
    """complete 顺序脚本：str 项为响应文本；Exception 项直接 raise。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


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
def mem(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "state.md").write_text(
        "# 现状快照\n\n## 角色现状\n\n## 世界现状\n", encoding="utf-8")
    return MemoryStore(tmp_path)


def loader(home):
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(home_dir=home)


def _events(summary: str, n: int = 1) -> list[dict]:
    return [{"title": f"事件{i}", "summary": summary, "location": "山寨",
             "time_marker": "深夜", "characters": [], "new_info": [],
             "foreshadow": ""} for i in range(1, n + 1)]


def _write_chapter(mem: MemoryStore, seq: int, title: str, events: list[dict]) -> None:
    mem.write_chapter_memory(seq, title, {"events": events})


async def test_maintain_appends_plot_section(mem, tmp_path):
    """节结构对拍：卷头 + 情节节 + 章段（### 第N章《title》）+ 事件行（含地点/时间注）。"""
    _write_chapter(mem, 1, "题一", _events("甲潜入山寨"))
    _write_chapter(mem, 2, "题二", _events("乙点火", n=2))
    keeper, llm, prompts = DigestKeeper(mem), FakeLLM([]), loader(tmp_path)

    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=1, plot_title="夜袭",
                          chapter_seqs=[1, 2])

    text = keeper.read(1)
    assert (tmp_path / "memory" / "digests" / "vol-01.md").exists()
    assert text.startswith("# 第1卷前情提要")
    assert "## 情节 1. 夜袭" in text
    assert "### 第1章《题一》" in text
    assert "### 第2章《题二》" in text
    assert "- 事件1——甲潜入山寨（山寨；深夜）" in text
    assert "- 事件2——乙点火（山寨；深夜）" in text
    # 未超 2000 字：零 LLM 调用
    assert llm.calls == []


async def test_maintain_idempotent_rerun(mem, tmp_path):
    """先清后建：同情节重跑整节替换——文件无重复节、两次结果逐字一致。"""
    _write_chapter(mem, 1, "题一", _events("甲潜入山寨"))
    keeper, llm, prompts = DigestKeeper(mem), FakeLLM([]), loader(tmp_path)
    kwargs = {"llm": llm, "prompts": prompts, "settings": make_settings(),
              "vol": 1, "plot_seq": 1, "plot_title": "夜袭", "chapter_seqs": [1]}

    await keeper.maintain(**kwargs)
    first = keeper.read(1)
    await keeper.maintain(**kwargs)
    second = keeper.read(1)

    assert first == second
    assert second.count("## 情节 1. 夜袭") == 1
    assert second.count("### 第1章《题一》") == 1


async def test_over_2000_compresses_oldest(mem, tmp_path):
    """超 2000 字：最早情节节被 lite mock 压缩（带（已压缩）标记），新节原文保留。"""
    long_summary = "甲" * 1200
    _write_chapter(mem, 1, "题一", _events(long_summary))
    _write_chapter(mem, 2, "题二", _events(long_summary))
    compressed = json.dumps({"segments": ["压缩后：夜袭成事"]}, ensure_ascii=False)
    keeper = DigestKeeper(mem)
    llm = FakeLLM([compressed])
    prompts = loader(tmp_path)

    # 先建情节 1（未超限，零压缩）
    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=1, plot_title="夜袭", chapter_seqs=[1])
    assert llm.calls == []
    # 再追加情节 2 → 整卷超 2000 字 → 压最早节（情节 1），本次新节（情节 2）原文保留
    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=2, plot_title="渡河", chapter_seqs=[2])

    text = keeper.read(1)
    assert len(text) <= DIGEST_LIMIT
    assert "## 情节 1. 夜袭（已压缩）" in text
    assert "压缩后：夜袭成事" in text
    assert "## 情节 2. 渡河" in text
    assert f"- 事件1——{long_summary}（山寨；深夜）" in text   # 新节原文未动
    assert len(llm.calls) == 1
    prompt = llm.calls[0]
    assert "1" in prompt and long_summary in prompt   # 渲染契约：vol + segments


async def test_compress_llm_failure_keeps_uncompressed(mem, tmp_path):
    """压缩失败降级：digest 保持冗长、原始段落不丢、不 raise、无（已压缩）标记。"""
    long_summary = "甲" * 1200
    _write_chapter(mem, 1, "题一", _events(long_summary))
    _write_chapter(mem, 2, "题二", _events(long_summary))
    keeper = DigestKeeper(mem)
    llm = FakeLLM([LLMError("boom", transient=True, kind="timeout")])
    prompts = loader(tmp_path)

    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=1, plot_title="夜袭", chapter_seqs=[1])
    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=2, plot_title="渡河", chapter_seqs=[2])

    text = keeper.read(1)
    assert len(text) > DIGEST_LIMIT          # 宁可冗长不可丢
    assert "（已压缩）" not in text
    assert f"- 事件1——{long_summary}（山寨；深夜）" in text


async def test_read_missing_returns_empty(mem):
    assert DigestKeeper(mem).read(9) == ""


async def test_digest_compress_usage_metered(mem, tmp_path):
    """压缩调用带 usage → UsageMeter.total 记到该次 usage（未超限的维护零调用、
    meter 不动）。"""
    from harness.tools.base import UsageMeter

    long_summary = "甲" * 1200
    _write_chapter(mem, 1, "题一", _events(long_summary))
    _write_chapter(mem, 2, "题二", _events(long_summary))
    compressed = json.dumps({"segments": ["压缩后：夜袭成事"]}, ensure_ascii=False)
    keeper = DigestKeeper(mem)
    llm = FakeLLM([compressed])
    prompts = loader(tmp_path)
    meter = UsageMeter()

    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=1, plot_title="夜袭", chapter_seqs=[1],
                          meter=meter)
    assert meter.total is None                     # 未超限：零压缩调用，不记账

    await keeper.maintain(llm=llm, prompts=prompts, settings=make_settings(),
                          vol=1, plot_seq=2, plot_title="渡河", chapter_seqs=[2],
                          meter=meter)
    assert meter.total == CompletionUsage(prompt_tokens=1, completion_tokens=1)
    assert "## 情节 1. 夜袭（已压缩）" in keeper.read(1)


async def test_rebuild_whole_vol(mem, tmp_path):
    """整卷先清后建：旧垃圾（含游离节）整卷重建后消失，情节节按 seq 升序。"""
    digests = tmp_path / "memory" / "digests"
    digests.mkdir(parents=True)
    (digests / "vol-01.md").write_text(
        "# 第1卷前情提要\n\n## 情节 9. 旧节\n\n垃圾\n", encoding="utf-8")
    _write_chapter(mem, 1, "题一", _events("甲潜入山寨"))
    _write_chapter(mem, 2, "题二", _events("乙点火"))
    keeper, llm, prompts = DigestKeeper(mem), FakeLLM([]), loader(tmp_path)

    await keeper.rebuild_async(
        llm=llm, prompts=prompts, settings=make_settings(), vol=1,
        plots=[{"seq": 2, "title": "渡河", "chapter_seqs": [2]},
               {"seq": 1, "title": "夜袭", "chapter_seqs": [1]}])

    text = keeper.read(1)
    assert "## 情节 9. 旧节" not in text
    assert text.index("## 情节 1. 夜袭") < text.index("## 情节 2. 渡河")
    assert "### 第1章《题一》" in text and "### 第2章《题二》" in text
    assert llm.calls == []
