"""harness/characters.py 的判定唯一源测试（spec X3）。

fixture：tmp 项目，直接经 ProjectStore 落情节卡 + 章卡（meta["units"] 带 characters），
覆盖：阈值上/下、集合名词整体剔除、override 文件扩展/坏行兜底、alias 算已建卡、
protagonists.txt 主角源、blocked_by_chapter 按章拦截、空项目不炸。
"""
from pathlib import Path

import pytest

from harness.characters import (
    BEAT_SHARE_THRESHOLD,
    COLLECTIVE_NAMES,
    blocked_by_chapter,
    carded_names,
    collect_candidates,
    load_collective_names,
)
from harness.store.project import ChapterDoc, ProjectStore


def _store(root: Path) -> ProjectStore:
    return ProjectStore(root)


def _unit(seq: int, characters: list[str]) -> dict:
    return {"seq": seq, "time": "清晨", "location": "山脚",
            "characters": characters, "event": f"事件{seq}", "covers": f"覆盖{seq}"}


def _chapter(store: ProjectStore, seq: int, units: list[dict]) -> None:
    store.write_chapter(ChapterDoc(
        seq=seq,
        meta={"seq": seq, "title": f"第{seq}章", "status": "draft", "units": units},
        body=f"正文{seq}"))


def _units(seq_start: int, char: str, n: int) -> list[dict]:
    return [_unit(seq_start + i, [char]) for i in range(n)]


def test_threshold_splits_candidates_and_walkons(tmp_path):
    store = _store(tmp_path)
    # 102 个拍：苏晚 4 个（share=4/102 ≈ 0.0392 ≥ 阈值）→ 候选；
    # 路人甲 2 个（≈0.0196）→ 龙套。末尾 2 个无角色名的拍**只进分母不进分子**。
    units = (_units(1, "苏晚", 4) + _units(5, "路人甲", 2) + _units(7, "路人乙", 94)
             + [_unit(101, []), _unit(102, ["  "])])
    _chapter(store, 1, units)
    analysis = collect_candidates(store)
    cand = {m.name for m in analysis.candidates}
    walk = {m.name for m in analysis.walk_ons}
    assert "苏晚" in cand
    assert "路人甲" in walk
    assert "苏晚" not in walk and "路人甲" not in cand
    su = next(m for m in analysis.candidates if m.name == "苏晚")
    assert su.beats == 4
    assert su.share == pytest.approx(4 / 102)
    assert su.chapters == 1
    assert su.first_chapter == 1


def test_collective_names_excluded(tmp_path):
    store = _store(tmp_path)
    # 众新弟子占一半 beats（share=0.5 远超阈值），也必须连龙套都不算
    units = _units(1, "众新弟子", 50) + _units(51, "苏晚", 50)
    _chapter(store, 1, units)
    analysis = collect_candidates(store)
    all_names = {m.name for lst in (analysis.protagonists, analysis.candidates, analysis.walk_ons)
                 for m in lst}
    assert "众新弟子" not in all_names
    assert "苏晚" in all_names
    # 默认表在模块常量里
    assert "众新弟子" in COLLECTIVE_NAMES
    assert isinstance(COLLECTIVE_NAMES, frozenset)


def test_collective_override_file_extends(tmp_path):
    store = _store(tmp_path)
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "collective_names.txt").write_text(
        "# 用户补充\n山贼众人\n\n", encoding="utf-8")
    units = _units(1, "山贼众人", 60) + _units(61, "苏晚", 40)
    _chapter(store, 1, units)
    analysis = collect_candidates(store)  # collective=None → 从 root 读 override
    all_names = {m.name for lst in (analysis.protagonists, analysis.candidates, analysis.walk_ons)
                 for m in lst}
    assert "山贼众人" not in all_names
    assert "苏晚" in {m.name for m in analysis.candidates}
    # 默认表仍生效（union，不是替换）
    assert "众新弟子" in load_collective_names(tmp_path)


def test_collective_override_bad_lines_fall_back(tmp_path):
    store = _store(tmp_path)
    harness = tmp_path / ".harness"
    harness.mkdir()
    # 注释、空行、纯空白行跳过；合法行仍生效
    (harness / "collective_names.txt").write_text(
        "# 注释行\n\n   \n山贼众人\n", encoding="utf-8")
    assert "山贼众人" in load_collective_names(tmp_path)
    assert "众新弟子" in load_collective_names(tmp_path)
    units = _units(1, "山贼众人", 60) + _units(61, "苏晚", 40)
    _chapter(store, 1, units)
    analysis = collect_candidates(store)
    assert "山贼众人" not in {m.name for m in analysis.candidates}
    # 整文件坏编码 → 默认表兜底，不抛
    (harness / "collective_names.txt").write_bytes(b"\xff\xfe\x00broken")
    assert load_collective_names(tmp_path) == COLLECTIVE_NAMES
    # 文件缺失 → 默认表
    (harness / "collective_names.txt").unlink()
    assert load_collective_names(tmp_path) == COLLECTIVE_NAMES


def test_alias_counts_as_carded(tmp_path):
    store = _store(tmp_path)
    store.upsert_character({"name": "苏晚晴", "role_type": "support",
                            "aliases": ["苏晚", "晚晴"]})
    store.upsert_character({"name": "林七", "role_type": "support"})
    _chapter(store, 1, _units(1, "苏晚", 50) + _units(51, "林七", 50))
    carded = carded_names(store)
    assert {"苏晚晴", "苏晚", "晚晴"} <= carded
    analysis = collect_candidates(store)
    assert analysis.uncarded_major(carded) == []
    assert blocked_by_chapter(store, analysis) == {}


def test_protagonists_txt(tmp_path):
    store = _store(tmp_path)
    store.upsert_character({"name": "林雪", "role_type": "protagonist"})
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "protagonists.txt").write_text(
        "# 注释\n陆沉舟\n\n叶知秋\n", encoding="utf-8")
    _chapter(store, 1, _units(1, "林雪", 1) + _units(2, "陆沉舟", 2) + _units(4, "苏晚", 97))
    analysis = collect_candidates(store)
    pro = {m.name for m in analysis.protagonists}
    assert {"林雪", "陆沉舟", "叶知秋"} <= pro
    # 主角不占候选/龙套坑（林雪 share=0.01 低于阈值也仍是主角）
    assert "林雪" not in {m.name for m in analysis.walk_ons}
    assert "林雪" not in {m.name for m in analysis.candidates}
    # 文件缺失 → 只有卡上的主角
    store2 = _store(tmp_path / "empty")
    (tmp_path / "empty" / ".harness").mkdir(parents=True)
    only = collect_candidates(store2)
    assert only.protagonists == []
    # major_names = 主角 + 候选
    assert set(analysis.major_names()) == pro | {m.name for m in analysis.candidates}


def test_blocked_by_chapter_per_chapter(tmp_path):
    store = _store(tmp_path)
    # 林七有卡（不算 blocked）；苏晚无卡、只在第 3 章出现 → 只拦第 3 章
    store.upsert_character({"name": "林七", "role_type": "support"})
    _chapter(store, 1, _units(1, "林七", 50))
    _chapter(store, 2, _units(51, "林七", 40))
    _chapter(store, 3, _units(91, "苏晚", 90) + _units(181, "林七", 20))
    result = blocked_by_chapter(store)
    assert result == {3: ["苏晚"]}
    for names in result.values():
        assert isinstance(names, list)


def test_empty_project(tmp_path):
    store = _store(tmp_path)
    analysis = collect_candidates(store)
    assert analysis.protagonists == []
    assert analysis.candidates == []
    assert analysis.walk_ons == []
    assert analysis.major_names() == []
    assert analysis.uncarded_major(set()) == []
    assert carded_names(store) == set()
    assert blocked_by_chapter(store, analysis) == {}
    assert BEAT_SHARE_THRESHOLD == 0.03
