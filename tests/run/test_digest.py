"""progress_digest：真项目（create_project 脚手架 + store 写入）上的行为测试。"""
import pytest

from harness.projects import create_project
from harness.run.digest import progress_digest
from harness.store.project import ChapterDoc, ProjectStore


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    return ProjectStore(info.path)


def test_digest_empty_project(store):
    text = progress_digest(store)
    assert "卷（0）" in text
    assert "章节（0）：done 0 / writing 0 / planned 0；最近：无" in text
    assert "角色（0）" in text
    assert "世界观（0）" in text


def test_digest_reports_volumes_plots_chapters_characters_world(store):
    store.upsert_arcs([
        {"vol": 1, "title": "崛起", "goal": "踏上修行路"},
        {"vol": 2, "title": "征战", "goal": "统一北境"},
    ])
    store.upsert_plots(1, [{"seq": 1, "title": "夜袭", "status": "approved"},
                           {"seq": 2, "title": "拜师", "status": "planned"}])
    store.upsert_plots(2, [{"seq": 1, "title": "出征", "status": "done"}])
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done"}, body="x"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "转折", "status": "writing"}, body="x"))
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    store.upsert_character({"name": "沈青", "role_type": "supporting", "aliases": []})
    store.upsert_world_entries([
        {"title": "境界体系", "category": "修炼", "summary": "s"},
        {"title": "功法", "category": "修炼", "summary": "s"},
        {"title": "黑水城", "category": "地理", "summary": "s"},
    ])
    text = progress_digest(store)
    # 卷行：seq + 标题 + 目标 + 每卷情节计数
    assert "- 第1卷 崛起 目标：踏上修行路" in text
    assert "情节 planned 1 / approved 1 / done 0" in text
    assert "- 第2卷 征战 目标：统一北境" in text
    assert "情节 planned 0 / approved 0 / done 1" in text
    # 章节总计数 + 最近一章
    assert "章节（2）：done 1 / writing 1 / planned 0；最近：第2章《转折》" in text
    # 角色数 + 名字（store 按文件名排序：沈青 < 陆昭）
    assert "角色（2）：沈青、陆昭" in text
    # 世界观条目数 + 分类计数
    assert "世界观（3）：修炼 2 / 地理 1" in text


def test_digest_is_deterministic(store):
    store.upsert_arcs([{"vol": 1, "title": "崛起", "goal": "g"}])
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done"}, body="x"))
    assert progress_digest(store) == progress_digest(store)


def test_digest_tolerates_corrupt_arcs(store):
    """坏 arcs.md 不再炸掉整个摘要（每步注入，一坏全 loop 停）：按零卷 + 其余节照出。"""
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done"}, body="x"))
    (store.root / "outline" / "arcs.md").write_text("---\narcs: [坏\n---\n", encoding="utf-8")
    text = progress_digest(store)
    assert isinstance(text, str)
    assert "卷（0）" in text
    assert "章节（1）：done 1" in text


def test_digest_sort_key_tolerates_mixed_non_int_vols(store):
    """双坏档（非 int 卷号不同类型）不炸排序键（str < int TypeError）；
    vol: true 是 int 子类但不当第 1 卷、不渲染「第True卷」，按缺号处理。"""
    store.upsert_arcs([
        {"vol": "一", "title": "坏卷甲", "goal": "g"},
        {"vol": "缺失", "title": "坏卷乙", "goal": "g"},
        {"vol": True, "title": "坏卷丙", "goal": "g"},
        {"vol": 2, "title": "正常卷", "goal": "g"},
    ])
    text = progress_digest(store)
    assert "卷（4）" in text
    assert "第True卷" not in text
    assert "（卷号缺失）" in text
    assert "- 第2卷 正常卷" in text
    assert progress_digest(store) == text  # 排序稳定，双跑一致
