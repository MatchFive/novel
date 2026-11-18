# MemoryStore：章记忆/现状投影文件读写（P4 Task 4）
"""测试钉死的文件格式（brief task-4）与 seed memory_service 语义投影：

- 章记忆 ``memory/<seq:04d>.md`` 四层往返（events/character_events/info_updates/
  promise_updates，字段=Task 5 解析产物的 seed 字段名）；
- 解析失败 read 返 None / read_state 返空 dict，不 raise；
- apply_state_updates：只写明确给出的字段（防手改被冲）/locked 跳过/单调守卫/
  status 合法值校验/last_seen 只增；
- rename_chapter_seq：文件改名 + 「第N章」锚点改写 + state.md 数值重映射。
"""
from __future__ import annotations

import pytest

from harness.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    (tmp_path / "memory").mkdir()
    # 脚手架同款三节空骨架（projects.py 建项目时的初始 state.md）
    (tmp_path / "memory" / "state.md").write_text(
        "# 现状快照\n\n## 角色现状\n\n## 世界现状\n", encoding="utf-8")
    return MemoryStore(tmp_path)


def _layers() -> dict:
    return {
        "events": [{
            "title": "夜探藏经阁",
            "summary": "林凡潜入藏经阁顶层",
            "location": "藏经阁",
            "time_marker": "深夜",
            "characters": ["林凡", "守阁长老"],
            "new_info": ["血脉图谱"],
            "foreshadow": "阁顶有暗格",
        }],
        "character_events": [{
            "character": "林凡",
            "summary": "翻窗而入",
            "quotes": "就是这里了",
            "acted": "潜入阁楼",
            "felt": "紧张",
            "got_lost": "",
            "secrets": "身怀逆天血脉",
        }],
        "info_updates": [{
            "title": "血脉图谱",
            "content": "记载逆天血脉的来历",
            "revealed_to": ["林凡"],
        }],
        "promise_updates": [{
            "kind": "foreshadow",
            "content": "阁顶暗格终将开启",
            "by_character": "林凡",
            "status": "pending",
            "due": 5,
            "resolution": "",
        }],
    }


def _state_path(store: MemoryStore):
    return store.root / "memory" / "state.md"


def test_write_read_roundtrip(store):
    layers = _layers()
    store.write_chapter_memory(1, "起程", layers)
    doc = store.read_chapter_memory(1)
    assert doc["seq"] == 1 and doc["title"] == "起程"
    # 四层全字段往返：seed 字段名原样回来
    assert doc["events"] == layers["events"]
    assert doc["character_events"] == layers["character_events"]
    assert doc["info_updates"] == layers["info_updates"]
    assert doc["promise_updates"] == layers["promise_updates"]
    # 知晓关系由 info_updates 的 revealed_to 投影
    assert doc["knowledge"] == [
        {"character": "林凡", "info_title": "血脉图谱", "chapter": 1}]
    # 五个 ## 节齐全（T3 build_corpus 按节切分的前提）
    text = (store.root / "memory" / "0001.md").read_text(encoding="utf-8")
    for section in ("## 事件", "## 角色经历", "## 信息项", "## 知晓关系", "## 承诺与伏笔"):
        assert section in text


def test_read_garbage_returns_none(store):
    path = store.root / "memory" / "0001.md"
    path.write_text("这不是记忆文件\n随便什么内容", encoding="utf-8")
    assert store.read_chapter_memory(1) is None
    path.write_bytes(b"\xff\xfe\x00bad")   # 非 UTF-8
    assert store.read_chapter_memory(1) is None
    assert store.read_chapter_memory(7) is None   # 文件缺失


def test_purge_idempotent(store):
    store.write_chapter_memory(1, "起程", _layers())
    store.write_chapter_memory(2, "风波", _layers())
    assert store.list_chapter_memories() == [1, 2]
    store.purge_chapter(1)
    assert store.list_chapter_memories() == [2]
    store.purge_chapter(1)    # 幂等：不存在则 no-op
    store.purge_chapter(99)   # 从未存在过也不炸
    assert not (store.root / "memory" / "0001.md").exists()
    assert store.read_chapter_memory(1) is None


def test_read_state_empty_skeleton_and_missing(store):
    # 脚手架三节空骨架 → 空 dict（不 raise）
    assert store.read_state() == {"characters": {}, "world": {}}
    _state_path(store).unlink()
    assert store.read_state() == {"characters": {}, "world": {}}   # 文件缺失
    _state_path(store).write_text("garbage", encoding="utf-8")
    assert store.read_state() == {"characters": {}, "world": {}}   # 解析失败


def test_apply_updates_preserves_unmentioned_fields_and_hand_edits(store):
    store.apply_state_updates({"characters": [
        {"name": "林凡", "realm": "凡界", "location": "青云山", "status": "alive"},
    ]}, 1)
    # 用户手改 location
    path = _state_path(store)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "location: 青云山", "location: 后山"), encoding="utf-8")
    # 再 apply 不含 location 的 updates：未提字段与手改都保留（seed :190-216）
    store.apply_state_updates({"characters": [{"name": "林凡", "notes": "得到图谱"}]}, 2)
    c = store.read_state()["characters"]["林凡"]
    assert c["location"] == "后山"      # 手改未被冲掉
    assert c["realm"] == "凡界"          # 未提字段保留原值
    assert c["notes"] == "得到图谱"
    assert c["status"] == "alive"
    assert c["updated"] == 2 and c["last_seen"] == 2


def test_apply_updates_skips_locked_and_monotonic(store):
    store.apply_state_updates({"characters": [
        {"name": "林凡", "location": "青云山", "status": "alive"},
    ]}, 1)
    # 手改锁定 location（seed :209-218：locked 列表内字段自动更新不得覆盖）
    path = _state_path(store)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "updated: 第1章", "locked: location｜updated: 第1章"), encoding="utf-8")
    store.apply_state_updates({"characters": [{"name": "林凡", "location": "后山"}]}, 2)
    c = store.read_state()["characters"]["林凡"]
    assert c["location"] == "青云山"     # locked 字段不动
    assert c["updated"] == 2
    # 单调守卫：seq=9 落定后，seq=5 的 updates 整角色跳过（seed :206）
    store.apply_state_updates({"characters": [
        {"name": "林凡", "notes": "n9", "realm": "魔界"},
    ]}, 9)
    c = store.read_state()["characters"]["林凡"]
    assert c["notes"] == "n9" and c["realm"] == "魔界" and c["updated"] == 9
    store.apply_state_updates({"characters": [
        {"name": "林凡", "notes": "n5", "realm": "凡界"},
    ]}, 5)
    c = store.read_state()["characters"]["林凡"]
    assert c["notes"] == "n9" and c["realm"] == "魔界" and c["updated"] == 9


def test_apply_updates_last_seen_monotonic_only(store):
    store.apply_state_updates({"characters": [{"name": "林凡", "realm": "凡界"}]}, 5)
    c = store.read_state()["characters"]["林凡"]
    assert c["last_seen"] == 5
    store.apply_state_updates({"characters": [{"name": "林凡"}]}, 9)
    c = store.read_state()["characters"]["林凡"]
    assert c["last_seen"] == 9           # 5→9 生效（seed :250-260 只增）
    # 手改把 last_seen 抬到 12（updated 仍为 9），seq=10 的 apply 不得把它退回 10
    path = _state_path(store)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "last_seen: 第9章", "last_seen: 第12章"), encoding="utf-8")
    store.apply_state_updates({"characters": [{"name": "林凡", "notes": "n"}]}, 10)
    c = store.read_state()["characters"]["林凡"]
    assert c["last_seen"] == 12          # 9→5（此处 12→10）拒绝
    assert c["notes"] == "n" and c["updated"] == 10


def test_apply_updates_status_validation(store):
    store.apply_state_updates({"characters": [
        {"name": "林凡", "status": "alive", "realm": "凡界"},
    ]}, 1)
    # 非法 status：照 seed 行为——该字段跳过，不 raise（seed :217-219 只接受合法值）
    store.apply_state_updates({"characters": [
        {"name": "林凡", "status": "undead", "level": "炼气"},
    ]}, 2)
    c = store.read_state()["characters"]["林凡"]
    assert c["status"] == "alive"        # 非法值被丢弃，原值保留
    assert c["level"] == "炼气"          # 同批合法字段照常写入
    assert c["updated"] == 2
    # 合法值照单全收
    store.apply_state_updates({"characters": [{"name": "林凡", "status": "dead"}]}, 3)
    assert store.read_state()["characters"]["林凡"]["status"] == "dead"


def test_rename_remaps_anchors_and_state(store):
    layers = _layers()
    layers["promise_updates"][0]["due"] = 1     # 到期锚点恰好=old，验证重映射
    store.write_chapter_memory(1, "起程", layers)
    # 另一章的承诺「到期第1章」——seed update_due_chapter 是全库重映射
    store.write_chapter_memory(3, "风波", {"promise_updates": [
        {"kind": "promise", "content": "长老之约", "by_character": "守阁长老",
         "status": "pending", "due": 1, "resolution": ""},
    ]})
    store.apply_state_updates({
        "characters": [{"name": "林凡", "realm": "凡界"}],
        "world": [{"kind": "sect", "key": "天玄宗", "label": "声望",
                   "value": "高", "importance": 4}],
    }, 1)
    store.rename_chapter_seq(1, 2)
    # 文件改名
    assert not (store.root / "memory" / "0001.md").exists()
    assert (store.root / "memory" / "0002.md").exists()
    assert store.list_chapter_memories() == [2, 3]
    # 本文件锚点：标题/信息项源自/知晓/承诺埋点与到期点全部 1→2
    doc = store.read_chapter_memory(2)
    assert doc["seq"] == 2
    assert doc["knowledge"][0]["chapter"] == 2
    assert doc["promise_updates"][0]["due"] == 2
    text = (store.root / "memory" / "0002.md").read_text(encoding="utf-8")
    assert text.startswith("# 第2章记忆《起程》")
    assert "源自第2章" in text and "第2章埋" in text
    assert "第1章" not in text
    # 跨文件到期锚点同样重映射（seed 仓储 update_due_chapter 的文件等价物）
    doc3 = store.read_chapter_memory(3)
    assert doc3["promise_updates"][0]["due"] == 2
    # state.md 数值重映射：updated / last_seen / 世界 updated
    state = store.read_state()
    c = state["characters"]["林凡"]
    assert c["updated"] == 2 and c["last_seen"] == 2
    assert state["world"][("sect", "天玄宗")]["updated"] == 2


def test_rename_chapter_seq_collision_fails_loudly(store):
    store.write_chapter_memory(1, "起程", _layers())
    store.write_chapter_memory(2, "风波", _layers())
    with pytest.raises(FileExistsError):
        store.rename_chapter_seq(1, 2)
    # 两个文件原样未动
    assert store.list_chapter_memories() == [1, 2]
