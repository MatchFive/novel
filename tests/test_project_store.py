import pytest

from harness.projects import create_project
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    return ProjectStore(info.path)


def test_chapter_round_trip(store):
    doc = ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done",
                                  "beats": [{"pov": "甲", "location": "村口", "event": "出场"}]},
                     body="第一章正文。\n\n第二段。")
    p = store.write_chapter(doc)
    assert p.name == "0001-开篇.md"
    got = store.read_chapter(1)
    assert got.meta["title"] == "开篇" and got.body == doc.body


def test_chapter_title_rename_file(store):
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "旧名", "status": "planned"}, body="x"))
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "新名", "status": "planned"}, body="x"))
    assert [p.name for p in (store.root / "chapters").iterdir()] == ["0001-新名.md"]


def test_read_chapter_fills_path(store):
    """read_chapter 填 ``path``（与 list_chapters 一致）→ 拿它的结果去就地改写时
    不会再落到「按 seq 再 glob 一次」的回退分支（glob 按的是**文件名前缀**，
    与 front-matter 章号不一致时会落到别的章）。"""
    written = store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇",
                                                          "status": "done"}, body="正文"))
    got = store.read_chapter(1)
    assert got.path is not None
    assert got.path == written
    assert got.path.name == "0001-开篇.md"


def test_world_upsert_merges_preserving_hand_edits(store):
    store.upsert_world_entries([{"title": "境界体系", "category": "修炼", "summary": "s", "content": "c", "importance": "高"}])
    world = store.root / "world.md"
    world.write_text(world.read_text(encoding="utf-8").replace("c", "用户手改"), encoding="utf-8")
    store.upsert_world_entries([{"title": "新增条目", "category": "地理", "summary": "s2", "content": "c2", "importance": "中"}])
    entries = store.read_world()
    hand = next(e for e in entries if e["title"] == "境界体系")
    assert "用户手改" in hand["content"]
    assert any(e["title"] == "新增条目" for e in entries)


def test_character_file_per_name_and_rename(store):
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    assert (store.root / "characters" / "陆昭.md").exists()
    store.rename_character("陆昭", "陆昭雪")
    assert not (store.root / "characters" / "陆昭.md").exists()
    assert store.read_character("陆昭雪")["role_type"] == "protagonist"


def test_plots_per_volume_file(store):
    store.upsert_plots(1, [{"seq": 1, "title": "夜袭", "status": "approved", "summary": "s"}])
    assert (store.root / "outline" / "plots" / "vol-01.md").exists()
    assert store.read_plots(1)[0]["title"] == "夜袭"
    assert store.read_plots() == store.read_plots(1)


def test_broken_frontmatter_raises_and_backs_up(store):
    (store.root / "chapters" / "0001-坏章.md").write_text("---\nseq: [坏\n---\n正文\n", encoding="utf-8")
    with pytest.raises(FileFormatError):
        store.read_chapter(1)
    assert (store.root / "chapters" / "0001-坏章.md.bak").exists()


def test_world_multiline_summary_round_trip(store):
    summary = "第一行摘要。\n第二行摘要。"
    store.upsert_world_entries(
        [{"title": "多行摘要", "category": "设定", "summary": summary,
          "content": "正文。", "importance": "低"}])
    got = next(e for e in store.read_world() if e["title"] == "多行摘要")
    assert got["summary"] == summary


def test_plot_multiline_kv_round_trip(store):
    """多行 KV 值（writer 渲染为续行）读回不再丢后续行。"""
    store.upsert_plots(1, [{"seq": 1, "title": "夜袭", "notes": "line1\nline2"}])
    got = store.read_plots(1)
    assert got[0]["notes"] == "line1\nline2"


def test_upsert_arcs_skips_non_dict_with_warning(store, caplog):
    """非 dict 条目跳过 + 警告，不再合并到 key None 互相覆盖。"""
    with caplog.at_level("WARNING", logger="store.project"):
        store.upsert_arcs([{"vol": 1, "title": "第一卷"}, "坏条目", 42,
                           {"vol": 2, "title": "第二卷"}])
    arcs = store.read_arcs()
    assert [a["vol"] for a in arcs] == [1, 2]
    assert "坏条目" in caplog.text


def test_upsert_arcs_merge_ignores_bool_vol(store):
    """手改 vol: true 与真 vol: 1 并存（True==1 索引碰撞会把真第 1 卷静默顶掉）：
    合并只认 int vol——vol 1 被新进条目原位替换，vol true 条目原样存活。"""
    store.upsert_arcs([{"vol": True, "title": "坏卷", "goal": "g"},
                       {"vol": 1, "title": "旧第一卷", "goal": "g"}])
    store.upsert_arcs([{"vol": 1, "title": "新第一卷", "goal": "g2"}])
    arcs = store.read_arcs()
    assert [a["vol"] for a in arcs] == [True, 1]      # 两条都在，坏卷未被吞
    assert arcs[1]["title"] == "新第一卷"               # 真第 1 卷原位更新
    assert arcs[0]["title"] == "坏卷"                   # vol true 条目原样保留


def test_list_characters_empty(store):
    assert store.list_characters() == []


def test_list_characters_two_cards_sorted_by_filename(store):
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    store.upsert_character({"name": "沈青", "role_type": "supporting", "aliases": []})
    cards = store.list_characters()
    assert [c["name"] for c in cards] == ["沈青", "陆昭"]
    assert cards[0] == {"name": "沈青", "role_type": "supporting", "aliases": []}
    assert cards[1] == {"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]}


def test_list_characters_skips_corrupt_card(store, caplog):
    """坏卡（front-matter 解析失败）跳过 + 警告，其余卡照列。"""
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    (store.root / "characters" / "坏卡.md").write_text("---\nname: [坏\n---\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="store.project"):
        cards = store.list_characters()
    assert [c["name"] for c in cards] == ["陆昭"]
    assert "坏卡" in caplog.text


def test_list_characters_aliases_scalar_not_split(store):
    """手改 front-matter 把 aliases 写成标量：整个字符串当一个别名，不拆字符。"""
    (store.root / "characters" / "手改.md").write_text(
        "---\nname: 陆昭\nrole_type: protagonist\naliases: 昭哥\n---\n\n# 陆昭\n", encoding="utf-8")
    cards = store.list_characters()
    assert cards == [{"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]}]
