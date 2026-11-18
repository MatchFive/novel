"""P13：三条 P1/P2 历史遗留按设计口径补齐。

1. **熔断按工具分桶**（设计 §1.2「**同工具**连续失败≥2」）——原先是**全局**计数：
   A 失败一次 + B 失败一次 → **B** 被冷却（B 只失败过一次），且 A 的失败「连坐」到
   毫不相干的工具上。
2. **planned_volume_count 持久化**（P2 backlog 第 18 行）：此前段二注入的「全书参考章数」
   一律用**实际弧数**顶替 → 「预计 20 卷、只规划了 3 卷」时参考章数被算成 1/3。
3. **state.json shape 校验**（P1 backlog 第 33 行）：JSON 合法但**形状不对** → 当坏档
   处理，**不**让 KeyError 穿出去（否则 `--resume` 带栈崩）。
"""
from __future__ import annotations

import json

from harness.run.state import RunState

# ------------------------------------------------------------------ #2 持久化

def test_planned_volume_count_persisted(tmp_path):
    """``planned_volume_count`` 与 arcs 同级落进 arcs.md 的 front-matter。"""
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲"}], planned_volume_count=20)

    assert store.read_planned_volume_count() == 20
    raw = (store.root / "outline" / "arcs.md").read_text(encoding="utf-8")
    assert "planned_volume_count" in raw


def test_planned_volume_count_kept_on_later_write(tmp_path):
    """一次**不带**该参数的写**不该**抹掉已有的预计卷数。"""
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲"}], planned_volume_count=20)
    store.upsert_arcs([{"vol": 2, "title": "卷乙"}])     # 不带 planned
    assert store.read_planned_volume_count() == 20


def test_planned_volume_count_absent_is_none(tmp_path):
    """没有该字段 → None（回落实际弧数）。"""
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲"}])
    assert store.read_planned_volume_count() is None


def test_broken_arcs_meta_does_not_crash(tmp_path):
    """坏 arcs.md → 读预计卷数**不抛**（只读路径降级）。"""
    from harness.store.project import ProjectStore

    store = ProjectStore(tmp_path)
    arcs_md = store.root / "outline" / "arcs.md"
    arcs_md.parent.mkdir(parents=True, exist_ok=True)
    arcs_md.write_text("---\narcs: [1, 2\n---\n", encoding="utf-8")
    assert store.read_planned_volume_count() is None


# ------------------------------------------------------------------ #3 shape 校验

def test_state_load_ok(tmp_path):
    """正常 state.json → 读得出。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({
        "status": "paused",
        "brief": {"raw": "写个仙侠", "deliverables": ["outline"]},
        "stats": {"step": 3},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }, ensure_ascii=False), encoding="utf-8")

    state = RunState.load(tmp_path)
    assert state is not None
    assert state.status == "paused"
    assert state.brief["raw"] == "写个仙侠"
    assert state.stats["step"] == 3


def test_state_load_missing_key_returns_none(tmp_path):
    """**合法 JSON 但缺键** → 按坏档处理（返回 None），**不抛 KeyError**。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    (root / "state.json").write_text(
        json.dumps({"status": "paused"}, ensure_ascii=False), encoding="utf-8")

    assert RunState.load(tmp_path) is None


def test_state_load_wrong_types_returns_none(tmp_path):
    """**类型不对**（brief 不是 dict / status 不是 str）→ 同样按坏档处理。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)

    (root / "state.json").write_text(json.dumps(
        {"status": "paused", "brief": [], "stats": {}}, ensure_ascii=False),
        encoding="utf-8")
    assert RunState.load(tmp_path) is None

    (root / "state.json").write_text(json.dumps(
        {"status": 1, "brief": {}, "stats": {}}, ensure_ascii=False),
        encoding="utf-8")
    assert RunState.load(tmp_path) is None


def test_state_load_not_an_object_returns_none(tmp_path):
    """顶层不是对象 → None。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    (root / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert RunState.load(tmp_path) is None


def test_state_load_legacy_without_goal(tmp_path):
    """P10 之前的旧文件（brief 无 goal）→ 补 None，**不报错**。"""
    root = tmp_path / ".harness"
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({
        "status": "paused",
        "brief": {"raw": "写个仙侠", "deliverables": ["outline"]},
        "stats": {"step": 1},
    }, ensure_ascii=False), encoding="utf-8")

    state = RunState.load(tmp_path)
    assert state is not None
    assert state.brief["goal"] is None
