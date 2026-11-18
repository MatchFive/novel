# MemoryBundle 五块组装（P4 Task 7 注入端）：对拍 seed memory_injector.py
#（时间锚点 _label:83 / 未来过滤 :90-97 / POV top-8 :119-141 / 他人每人 top-3 :146-172 /
# knowledge [:10] :175-191 / related_events top-3 :194-214 / open_promises 置顶+[:6] :219-245）
# 与 seed assembler.py（_OFFSTAGE_STATUS:110 / _state_line:124-149 / _retrieve_states:550-563）。
"""注入端契约测试：五块字段/时间锚点/未来过滤（Review Focus #2）/top-k 口径/零命中兜底/
承诺置顶截断/state_line 与 world_state_block 文案对拍/digest 未来过滤。

夹具走真项目（tmp_path + create_project + MemoryStore 写盘）——本模块是
**记忆文件 → 提示词注入块**的接缝，测试必须钉死「只经 MemoryStore 读」的取值口径。
"""
import pytest

from harness.memory import MemoryStore
from harness.memory.inject import (
    MemoryBundle,
    build_memory_bundle,
    filter_future_digest,
    state_line,
    world_state_block,
)
from harness.projects import create_project
from harness.retrieval.corpus import Index, build_corpus


@pytest.fixture()
def mem(tmp_path):
    info = create_project(tmp_path, "注入书")
    return MemoryStore(info.path)


def _layers(events=None, cevents=None, infos=None, promises=None) -> dict:
    return {"events": events or [], "character_events": cevents or [],
            "info_updates": infos or [], "promise_updates": promises or []}


def _event(seq_marker: str, **kw) -> dict:
    ev = {"title": f"事件{seq_marker}", "summary": f"事件摘要{seq_marker}",
          "location": "", "time_marker": "", "characters": []}
    ev.update(kw)
    return ev


def _cevent(character: str, marker: str, **kw) -> dict:
    ce = {"character": character, "summary": f"{character}经历{marker}",
          "quotes": "", "acted": "", "felt": "", "got_lost": "", "secrets": ""}
    ce.update(kw)
    return ce


def _promise(marker: str, **kw) -> dict:
    pr = {"kind": "promise", "content": f"承诺{marker}", "by_character": "甲",
          "status": "open", "due": None, "resolution": ""}
    pr.update(kw)
    return pr


def _bundle(mem, *, pov="林凡", cast_names=(), cur_seq=5, index=None, query=""):
    return build_memory_bundle(mem=mem, index=index, pov=pov, cast_names=list(cast_names),
                               cur_seq=cur_seq, query=query)


# ================================================================ Review Focus #2：未来过滤

def test_bundle_future_materials_excluded(mem):
    """cur_seq=5 时第 6/7 章的记忆/承诺绝不出现在任何块（Review Focus #2）。"""
    for seq in (1, 2, 3, 4, 5, 6, 7):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            events=[_event(f"标记{seq}号")],
            cevents=[_cevent("林凡", f"标记{seq}号")],
            infos=[{"title": f"图谱{seq}号", "content": "c", "revealed_to": ["林凡"]}],
            promises=[_promise(f"标记{seq}号")],
        ))
    b = _bundle(mem, cast_names=["甲"])
    blob = f"{b.pov_memories}\n{b.other_char_events}\n{b.knowledge_state}\n" \
           f"{b.related_events}\n{b.open_promises}"
    for seq in (6, 7):
        assert f"标记{seq}号" not in blob
        assert f"[第{seq}章]" not in blob
        assert f"图谱{seq}号" not in blob
    # 本章及更早照常注入
    assert "标记5号" in b.pov_memories
    assert "图谱3号" in b.knowledge_state
    assert "承诺标记5号" in b.open_promises


# ================================================================ 时间锚点 + POV top-8

def test_bundle_time_anchors_and_pov_top8(mem):
    layers1 = _layers(cevents=[_cevent(
        "林凡", "全字段",
        quotes="我命由我", acted="出手救人", felt="心中一凛",
        got_lost="得铜镜", secrets="身负血脉")])
    mem.write_chapter_memory(1, "起程", layers1)
    for seq in (2, 3, 4, 5):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            cevents=[_cevent("林凡", f"{seq}号{i}") for i in range(4)]))
    b = _bundle(mem)  # 无查询 → 零命中兜底最近 8 条
    lines = b.pov_memories.splitlines()
    assert len(lines) == 8                       # top-8 硬截断（1+16=17 条取 8）
    assert all(line.startswith("· [第") for line in lines)
    # 时间锚点 [第N章]（seed _label:83）；最近者优先，第1章的唯一一条被挤掉
    assert lines[0].startswith("· [第5章]")
    assert "[第1章]" not in b.pov_memories


def test_pov_event_line_format(mem):
    """seed _format_event_line 逐字字段序：summary；台词：「」；作为：；感受：；得失：；秘密：。"""
    mem.write_chapter_memory(1, "单章", _layers(
        cevents=[_cevent("林凡", "格式验证", quotes="我命由我", acted="出手救人",
                         felt="心中一凛", got_lost="得铜镜", secrets="身负血脉")]))
    b = _bundle(mem, cur_seq=9)
    assert b.pov_memories.splitlines()[0] == (
        "· [第1章] 林凡经历格式验证；台词：「我命由我」；作为：出手救人；"
        "感受：心中一凛；得失：得铜镜；秘密：身负血脉")


# ================================================================ 其他角色每人 top-3

def test_bundle_other_chars_top3_each(mem):
    for i, seq in enumerate((1, 2, 3, 4, 5), 1):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            cevents=[_cevent("乙", f"{i}号"), _cevent("丙", f"{i}号")]))
    b = _bundle(mem, cast_names=["林凡", "乙", "丙"])
    out = b.other_char_events
    # 每人 top-3：最早的第 1 条被截掉
    assert "乙经历1号" not in out and "丙经历1号" not in out
    assert "乙经历5号" in out and "丙经历5号" in out
    assert "乙经历4号" in out and "丙经历4号" in out
    # POV 本人不进他人块
    assert "林凡经历" not in out


# ================================================================ 承诺置顶 + [:6]

def test_bundle_promises_urgent_first_capped6(mem):
    mem.write_chapter_memory(1, "埋承诺", _layers(promises=[
        _promise("A号", due=10),                  # 非急
        _promise("B号", due=2),                   # 急·已到期
        _promise("C号", due=None),                # 无到期（非急，第 7 位被截）
        _promise("D号", due=6),                   # 急·临近（6 ≤ 5+3）
        _promise("E号", due=4),                   # 急
        _promise("F号", due=3),                   # 急·已到期
        _promise("G号", kind="foreshadow", due=2),   # 急·伏笔（与 B 同 due，稳定序随原文）
        _promise("H号", status="resolved"),       # 已回收 → 不在未回收块
    ]))
    b = _bundle(mem, cur_seq=5)
    lines = b.open_promises.splitlines()
    assert len(lines) == 7                        # 6 条 + 溢出提示行
    assert "H号" not in b.open_promises
    assert lines[-1] == "· …共 7 条未兑现，详见记忆库"
    # 急置前置：due 升序在前（2,2,3,4,6），非急在后
    heads = [line.split("]")[0] for line in lines[:6]]
    assert "已到期（计划第2章回收）" in heads[0]
    assert "已到期（计划第2章回收）" in heads[1]   # G 伏笔与 B 同 due
    assert "已到期（计划第3章回收）" in heads[2]
    assert "临近到期（计划第6章回收）" in heads[4]
    assert "计划第10章回收" in heads[5]
    # kind 标注：承诺/伏笔/悬念
    assert any(line.startswith("· [承诺｜") for line in lines)
    assert lines[1].startswith("· [伏笔｜")
    # made 锚点
    assert all("第1章埋" in line for line in lines[:6])


# ================================================================ 零命中兜底

def test_bundle_zero_hit_pov_fallback_recent(mem):
    for seq in (1, 2, 3):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            cevents=[_cevent("林凡", f"{seq}号")]))
    b = _bundle(mem, query="饕餮龘靐齾")  # 有查询词但零命中 → 最近兜底
    lines = b.pov_memories.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("· [第3章]")
    assert lines[-1].startswith("· [第1章]")


# ================================================================ related_events 经 Index 的 mem: 语料

def test_bundle_related_events_via_index(mem):
    for seq in (1, 2, 3):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            events=[_event(f"{seq}号", summary=f"血脉图谱异动{seq}号")]))
    index = Index(build_corpus(mem.root))
    b = _bundle(mem, index=index, query="血脉图谱")
    lines = b.related_events.splitlines()
    assert len(lines) == 3                        # top-3
    assert all(line.startswith("· [第") for line in lines)
    assert "血脉图谱异动1号" in b.related_events
    # index=None → 相关历史事件块为空（不注入无关内容）
    b2 = _bundle(mem, index=None, query="血脉图谱")
    assert b2.related_events == ""


# ================================================================ state_line / world_state_block（seed 对拍）

def test_state_line_and_world_block(mem):
    line = state_line("林凡", {"realm": "凡界", "level": "3", "location": "山门",
                               "affiliation": "青石宗", "notes": "失忆中",
                               "status": "dead"})
    assert line == ("  【当前状态（实际，以此为准）】修为/等级：凡界 Lv3；所在：山门；"
                    "阵营：青石宗；失忆中；状态：已死亡")
    assert state_line("乙", {"last_seen": 5}) == ""          # 无可展示字段 → 空串
    assert state_line("乙", {"level": "3"}) == \
        "  【当前状态（实际，以此为准）】修为/等级：Lv3"
    assert state_line("丙", {"status": "exited"}) == \
        "  【当前状态（实际，以此为准）】状态：已退场"
    mem.apply_state_updates({"world": [
        {"kind": "sect", "key": "天玄宗", "label": "声望", "value": "100",
         "importance": 5},
        {"kind": "resource", "key": "灵石", "label": "灵石矿脉", "value": "三品",
         "importance": 9},
        {"kind": "sect", "key": "玄剑门", "label": "声望", "value": "低",
         "importance": 1},
    ]}, seq=1)
    out = world_state_block(mem.read_state(), top_k=8)
    rows = out.splitlines()
    assert rows[0] == "- [resource] 灵石矿脉：三品"   # importance 降序
    assert "- [sect] 声望：100" in out
    assert "- [sect] 声望：低" in out
    assert world_state_block({"world": {}}) == ""


# ================================================================ digest 未来过滤

def test_filter_future_digest():
    text = ("# 第1卷前情提要\n\n## 情节 1. 入门\n\n### 第1章《开篇》\n"
            "- 起程——离开山村\n\n### 第7章《未来》\n- 未来事件爆发\n")
    out = filter_future_digest(text, cur_seq=5)
    assert "### 第7章" not in out and "未来事件爆发" not in out
    assert "## 情节 1. 入门" in out and "- 起程——离开山村" in out
    # 全未来 → 空串；无 future → 原样保留
    future_only = "### 第9章《x》\n- y\n"
    assert filter_future_digest(future_only, cur_seq=5) == ""
    assert "起程" in filter_future_digest(text, cur_seq=7)


def test_filter_future_digest_drops_future_section_header():
    """M-b：整节无幸存章段 → 连「## 情节 N. title」节头一起丢（乱序写作时未来情节
    标题不得泄漏进提示词）；混合节（有幸存章段）节头保留。"""
    text = ("# 第1卷前情提要\n\n## 情节 1. 入门\n\n### 第1章《开篇》\n"
            "- 起程——离开山村\n\n## 情节 2. 决战\n\n### 第9章《未来》\n"
            "- 未来事件爆发\n\n### 第8章《也是未来》\n- 另一未来事件\n")
    out = filter_future_digest(text, cur_seq=5)
    assert "## 情节 2. 决战" not in out and "决战" not in out
    assert "未来事件爆发" not in out and "另一未来事件" not in out
    assert "## 情节 1. 入门" in out and "- 起程——离开山村" in out
    # 混合节：部分章段幸存 → 节头保留、未来段仍丢
    mixed = ("## 情节 3. 混合\n\n### 第2章《旧》\n- 旧事件\n\n"
             "### 第9章《未来》\n- 未来事件\n")
    out2 = filter_future_digest(mixed, cur_seq=5)
    assert "## 情节 3. 混合" in out2 and "- 旧事件" in out2
    assert "未来事件" not in out2


# ================================================================ MemoryBundle 字段

def test_bundle_fields_and_any_hits(mem):
    b = MemoryBundle()
    assert b.pov_memories == b.other_char_events == b.knowledge_state == ""
    assert b.related_events == b.open_promises == ""
    assert not b.any_hits()
    b.open_promises = "· [承诺｜第1章埋] x"
    assert b.any_hits()


# ================================================================ related_events 未来过滤（修复轮补钉）

def test_bundle_related_events_future_excluded(mem):
    """语料含未来章记忆时，>cur_seq 的事件节不进相关历史事件块（Review Focus #2）。

    判别性（终审 I-2）：未来章造短造密（短正文 + 关键词高密度）——BM25 长度归一化下
    短密文档天然高分；若实现漏掉 seq>cur_seq 过滤，它必入 top-3，本测试必红。
    """
    for seq in (1, 2, 3):
        mem.write_chapter_memory(seq, f"第{seq}章", _layers(
            events=[_event(f"{seq}号",
                           summary=f"血脉图谱异动{seq}号，众人围观测灵石异象良久")]))
    mem.write_chapter_memory(7, "第七章", _layers(
        events=[_event("未来号", summary="血脉图谱血脉图谱血脉图谱")]))
    index = Index(build_corpus(mem.root))
    b = _bundle(mem, index=index, cur_seq=3, query="血脉图谱")
    assert "血脉图谱血脉图谱血脉图谱" not in b.related_events
    assert "[第7章]" not in b.related_events
    assert len(b.related_events.splitlines()) == 3
    assert "血脉图谱异动1号" in b.related_events
