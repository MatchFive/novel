"""只读工具集：真项目（create_project 脚手架 + store 写入）上的行为测试。"""
import pytest

from harness.projects import create_project
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.read import (
    READ_ONLY_TOOLS,
    get_character,
    get_project_state,
    get_world,
    list_characters,
    list_world,
)


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    return ProjectStore(info.path)


@pytest.fixture
def populated(store):
    store.upsert_arcs([
        {"vol": 1, "title": "崛起", "goal": "踏上修行路"},
        {"vol": 2, "title": "征战", "goal": "统一北境"},
    ])
    store.upsert_plots(1, [{"seq": 1, "title": "夜袭", "status": "approved"},
                           {"seq": 2, "title": "拜师", "status": "planned"}])
    store.upsert_plots(2, [{"seq": 1, "title": "出征", "status": "done"}])
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done"}, body="x"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "转折", "status": "writing"}, body="x"))
    store.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥", "昭公子"]})
    store.upsert_character({"name": "沈青", "role_type": "supporting", "aliases": []})
    store.upsert_world_entries([
        {"title": "境界体系", "category": "修炼", "summary": "s"},
        {"title": "功法", "category": "修炼", "summary": "s"},
        {"title": "黑水城", "category": "地理", "summary": "s"},
    ])
    return store


def test_read_only_tools_cover_the_five():
    assert set(READ_ONLY_TOOLS) == {
        "get_project_state", "list_characters", "get_character", "list_world", "get_world",
    }


def test_get_project_state_empty(store):
    text = get_project_state(store, {})
    assert "卷（0）" in text
    assert "章节（0）" in text
    assert "角色（0）" in text
    assert "世界观（0）" in text


def test_get_project_state_populated(populated):
    text = get_project_state(populated, {})
    assert "卷（2）" in text
    assert "第1卷 崛起" in text
    assert "1. 夜袭" in text and "2. 拜师" in text  # 情节树
    assert "第2卷 征战" in text and "1. 出征" in text
    assert "章节（2）" in text and "开篇" in text and "转折" in text
    assert "陆昭" in text and "沈青" in text  # 角色名列表
    assert "境界体系" in text and "黑水城" in text  # 设定标题列表


def test_get_project_state_tolerates_corrupt_arcs(populated):
    (populated.root / "outline" / "arcs.md").write_text("---\narcs: [坏\n---\n", encoding="utf-8")
    text = get_project_state(populated, {})
    assert "卷（0）" in text
    assert "arcs" in text  # 输出里注明坏档
    assert "章节（2）" in text  # 其余节照出


def test_list_characters_empty(store):
    text = list_characters(store, {})
    assert "无角色" in text


def test_list_characters_lines(populated):
    text = list_characters(populated, {})
    assert "陆昭" in text and "protagonist" in text and "昭哥" in text
    assert "沈青" in text and "supporting" in text


def test_get_character_found(populated):
    text = get_character(populated, {"name": "陆昭"})
    assert "陆昭" in text
    assert "protagonist" in text
    assert "昭哥" in text


def test_get_character_not_found(populated):
    text = get_character(populated, {"name": "查无此人"})
    assert "未找到" in text and "查无此人" in text


def test_list_world_empty(store):
    text = list_world(store, {})
    assert "无世界观" in text


def test_list_world_lines(populated):
    text = list_world(populated, {})
    assert "境界体系" in text and "修炼" in text
    assert "黑水城" in text and "地理" in text


def test_get_world_exact_hit(populated):
    text = get_world(populated, {"title": "功法"})
    assert "功法" in text


def test_get_world_contains_hit(populated):
    text = get_world(populated, {"title": "境界"})
    assert "境界体系" in text


def test_get_world_case_sensitive(populated):
    populated.upsert_world_entries([{"title": "SP角色组", "category": "杂项", "summary": "s"}])
    # 小写查询不该命中含大写字母的标题（大小写敏感，精确与包含都不中）
    text = get_world(populated, {"title": "sp角色组"})
    assert "未找到" in text
    # 大写精确命中
    assert "SP角色组" in get_world(populated, {"title": "SP角色组"})


def test_get_world_not_found(populated):
    text = get_world(populated, {"title": "不存在的设定"})
    assert "未找到" in text and "不存在的设定" in text


def test_get_world_empty_title(populated):
    """空/纯空白标题不静默包含匹配第一条，给出明确提示。"""
    text = get_world(populated, {"title": ""})
    assert "请提供设定标题" in text
    assert "境界体系" not in text
    assert get_world(populated, {"title": "   "}) == text  # 纯空白同路


def test_get_project_state_mixed_non_int_vols_no_crash(populated):
    """双坏档（非 int 卷号不同类型）不炸排序键（str < int TypeError）；
    vol: true 不当第 1 卷、不渲染「第True卷」，按缺号处理；排序稳定。
    （store 合并索引已 fail-closed：vol true 不再顶掉真第 1 卷，坏卷丙原样并存。）"""
    populated.upsert_arcs([
        {"vol": "一", "title": "坏卷甲"},
        {"vol": "缺失", "title": "坏卷乙"},
        {"vol": True, "title": "坏卷丙"},
    ])
    text = get_project_state(populated, {})
    assert "卷（5）" in text
    assert "第True卷" not in text
    assert text.count("（卷号缺失）") == 3
    assert "- 第1卷" in text and "- 第2卷" in text
    assert get_project_state(populated, {}) == text
