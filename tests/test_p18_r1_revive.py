"""P18：**R1 复活**（数值单调回退）——给章记忆加第 5 层「状态更新」。

**病根**（2026-11-15 独立审核 D5）：``apply_state_updates`` 把每章的 state_updates
投影进 ``state.md`` 后**就丢弃了历史**（章记忆只有四层，没有 state_updates 层）→
审计的 R1 拿不到「相邻两次更新」→ **恒空**（设计 §2.10 的「现状数值单调性」只有形
没有实，验收表却写「确定性四规则全量」）。

**修法**：章记忆加第 5 节「状态更新」（逐字段一行 ``- 角色｜字段：值``）→ extract
写入 → store 解析 → audit 取**相邻两次更新**喂 R1。

**旧项目照旧**：记忆是四层的（无本节）→ 解析出空列表 → R1 仍不产出候选
（没有多点历史就是没有，不造数据）。

本文件钉死：① store 往返 + 向后兼容；② extract 写本节（含「合成的 seen 条目不记」）；
③ **集成层** R1 用例（这是 D5 点名的盲区：此前只有纯函数合成双点数据的测试）。
"""
from __future__ import annotations

import json

from harness.audit.rules import AuditFinding
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

# ------------------------------------------------------------------ store 往返

def _layers(state_updates: list[dict] | None = None) -> dict:
    return {
        "events": [{"title": "夜探山寨", "summary": "甲潜入山寨", "location": "山寨",
                    "time_marker": "深夜", "characters": ["甲"], "new_info": [],
                    "foreshadow": ""}],
        "character_events": [{"character": "甲", "summary": "翻进寨墙", "quotes": "就是这儿",
                              "acted": "潜入", "felt": "紧张", "got_lost": "", "secrets": ""}],
        "info_updates": [],
        "promise_updates": [],
        "state_updates": state_updates or [],
    }


def test_state_layer_roundtrip(tmp_path):
    """第 5 层往返：写进去的「角色｜字段：值」逐条原样解析回来。"""
    mem = MemoryStore(tmp_path)
    mem.write_chapter_memory(1, "起程", _layers([
        {"character": "甲", "field": "level", "value": "9"},
        {"character": "甲", "field": "realm", "value": "筑基后期"},
    ]))

    data = mem.read_chapter_memory(1)
    assert data["state_updates"] == [
        {"character": "甲", "field": "level", "value": "9"},
        {"character": "甲", "field": "realm", "value": "筑基后期"},
    ]
    text = (tmp_path / "memory" / "0001.md").read_text(encoding="utf-8")
    assert "## 状态更新" in text
    assert "- 甲｜level：9" in text


def test_state_layer_value_may_contain_colon(tmp_path):
    """值里允许有「：」——字段名取第一个「：」之前，其余全归值。"""
    mem = MemoryStore(tmp_path)
    mem.write_chapter_memory(1, "起程", _layers(
        [{"character": "甲", "field": "notes", "value": "伤：左肩"}]))
    data = mem.read_chapter_memory(1)
    assert data["state_updates"] == [
        {"character": "甲", "field": "notes", "value": "伤：左肩"}]


def test_old_four_layer_memory_parses_with_empty_state_layer(tmp_path):
    """**向后兼容**：四层老文件（无「状态更新」节）→ 解析照常，本层为空列表。"""
    mem = MemoryStore(tmp_path)
    mem.write_chapter_memory(1, "起程", _layers())
    text = (tmp_path / "memory" / "0001.md").read_text(encoding="utf-8")
    # 手工去掉本节（模拟旧版写出的文件）
    text = text.split("## 状态更新")[0]
    (tmp_path / "memory" / "0001.md").write_text(text, encoding="utf-8")

    data = mem.read_chapter_memory(1)
    assert data is not None
    assert data["state_updates"] == []
    assert data["events"][0]["title"] == "夜探山寨"      # 其余四层照常


# ------------------------------------------------------------------ extract 写本节

class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage = None


class FakeLLM:
    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append(messages[-1]["content"])
        return _Resp(self._script.pop(0))


async def test_extract_writes_state_layer(tmp_path):
    """提取把**模型明确给出的**角色状态字段写进第 5 层（逐字段一条）。"""
    from harness.config import ProviderConfig, Settings
    from harness.memory.extract import extract_chapter_memory
    from harness.prompts.loader import PromptLoader

    settings = Settings(
        library_root=tmp_path / "lib",
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test", default_tier="standard", web_verify=True)
    (tmp_path / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    prompts = PromptLoader(home_dir=tmp_path)
    mem = MemoryStore(tmp_path)

    payload = json.dumps({
        "events": [{"title": "夜探", "summary": "s", "location": "l", "time_marker": "",
                    "characters": ["甲", "乙"], "new_info": [], "foreshadow": ""}],
        "character_events": [{"character": "乙", "summary": "点火", "quotes": "走",
                              "acted": "", "felt": "", "got_lost": "", "secrets": ""}],
        "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [
            {"name": "甲", "level": 12, "realm": "筑基后期", "location": "山寨"},
        ], "world": []},
    }, ensure_ascii=False)

    status = await extract_chapter_memory(
        mem=mem, llm=FakeLLM([payload]), prompts=prompts, settings=settings,
        doc=ChapterDoc(seq=1, meta={"seq": 1, "title": "题一"}, body="甲拔刀。"),
        cast_cards=[{"name": "甲", "role_type": "主角", "profile": {}},
                    {"name": "乙", "role_type": "配角", "profile": {}}])

    assert status == "extracted"
    data = mem.read_chapter_memory(1)
    got = {(u["character"], u["field"]): u["value"] for u in data["state_updates"]}
    assert got == {("甲", "realm"): "筑基后期", ("甲", "level"): "12",
                   ("甲", "location"): "山寨"}
    # **合成的 seen 条目不记**（乙只出场、模型没给现状 → 没有字段可记）
    assert not [u for u in data["state_updates"] if u["character"] == "乙"]


# ------------------------------------------------------------------ 集成层 R1（D5 的盲区）

def _done_chapter(store: ProjectStore, seq: int, body: str) -> None:
    store.write_chapter(ChapterDoc(
        seq=seq, meta={"seq": seq, "title": f"第{seq}章", "status": "done",
                       "beats": [{"pov": "甲"}]},
        body=body))


def _project(tmp_path, name="单调书"):
    info = create_project(tmp_path, name)
    store = ProjectStore(info.path)
    mem = MemoryStore(info.path)
    return info, store, mem


async def test_r1_fires_with_real_history(tmp_path):
    """**集成层**：两章记忆给出同一角色的同一数值字段 → R1 真的产出候选。

    这是 2026-11-15 审核 D5 点名的盲区——此前 R1 只有纯函数合成双点数据的测试，
    集成层没有任何 R1 用例（真实项目上它恒空）。
    """
    from harness.audit.runner import run_audit

    info, store, mem = _project(tmp_path)
    _done_chapter(store, 1, "甲练刀至夜深。")
    _done_chapter(store, 2, "甲再度出关。")
    mem.write_chapter_memory(1, "第一夜", _layers(
        [{"character": "甲", "field": "level", "value": "9"}]))
    mem.write_chapter_memory(2, "再出关", _layers(
        [{"character": "甲", "field": "level", "value": "7"}]))
    mem.apply_state_updates({"characters": [{"name": "甲", "level": 7}]}, seq=2)

    events: list[tuple[str, dict]] = []
    text = await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[], emit=lambda k, p: events.append((k, p)))

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].title == "数值回退"
    assert pending[0].chapter == 2
    assert "9" in pending[0].detail and "7" in pending[0].detail
    assert ("audit_done", {"deterministic": 1, "llm": 0, "discarded": 0}) in events
    assert "确定性 1 条" in text.splitlines()[0]


async def test_r1_silent_without_history(tmp_path):
    """**没有多点历史就不报**（老项目：四层记忆 → R1 仍恒空，不造数据）。"""
    from harness.audit.runner import run_audit

    info, store, mem = _project(tmp_path, "老项目")
    _done_chapter(store, 1, "甲练刀至夜深。")
    _done_chapter(store, 2, "甲再度出关。")
    # 记忆是四层的（无「状态更新」节），state.md 只有最新值
    mem.write_chapter_memory(1, "第一夜", _layers())
    mem.write_chapter_memory(2, "再出关", _layers())
    mem.apply_state_updates({"characters": [{"name": "甲", "level": 7}]}, seq=2)

    text = await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                           chapters=[], emit=lambda k, p: None)

    assert IssuesStore(info.path).list("pending") == []
    assert "零发现" in text.splitlines()[0]


async def test_r1_decline_event_suppresses_finding(tmp_path):
    """区间内有跌落标记事件（重伤/跌落…）→ 不报数值回退（规则自身的守卫）。"""
    from harness.audit.runner import run_audit

    info, store, mem = _project(tmp_path, "有跌落")
    _done_chapter(store, 1, "甲练刀至夜深。")
    _done_chapter(store, 2, "甲重伤闭关。")
    mem.write_chapter_memory(1, "第一夜", _layers(
        [{"character": "甲", "field": "level", "value": "9"}]))
    layers = _layers([{"character": "甲", "field": "level", "value": "7"}])
    layers["events"] = [{"title": "甲重伤", "summary": "甲重伤，修为跌落",
                         "location": "洞府", "time_marker": "", "characters": ["甲"],
                         "new_info": [], "foreshadow": ""}]
    mem.write_chapter_memory(2, "闭关", layers)
    mem.apply_state_updates({"characters": [{"name": "甲", "level": 7}]}, seq=2)

    await run_audit(store=store, mem=mem, llm=None, prompts=None, settings=None,
                    chapters=[], emit=lambda k, p: None)

    assert IssuesStore(info.path).list("pending") == []


def test_audit_finding_shape_unchanged():
    """R1 产出形状不变（纯函数层）：rule="monotonic"、quote 带旧→新。"""
    from harness.audit.rules import check_monotonic

    findings = check_monotonic(
        [{"character": "甲", "field": "level", "value": "9", "chapter": 1},
         {"character": "甲", "field": "level", "value": "7", "chapter": 2}],
        [])
    assert len(findings) == 1
    assert isinstance(findings[0], AuditFinding)
    assert findings[0].rule == "monotonic" and findings[0].chapter == 2
