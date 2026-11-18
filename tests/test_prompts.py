import pytest

from harness.prompts.loader import (
    PromptLoader,
    PromptRenderError,
    insert_before_output_section,
)


@pytest.fixture
def loader(tmp_path):
    home = tmp_path / "home"; proj = tmp_path / "proj"
    (home / ".harness" / "prompts").mkdir(parents=True)
    (proj / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(project_root=proj, home_dir=home), home, proj


def test_builtin_fallback(loader):
    l, _, _ = loader
    assert "通用写作规则" in l.get("global.base_prompt")


def test_home_override_beats_builtin(loader):
    l, home, _ = loader
    (home / ".harness" / "prompts" / "global.base_prompt.md").write_text("用户版", encoding="utf-8")
    assert l.get("global.base_prompt") == "用户版"


def test_project_override_beats_home(loader):
    l, home, proj = loader
    (home / ".harness" / "prompts" / "global.base_prompt.md").write_text("用户版", encoding="utf-8")
    (proj / ".harness" / "prompts" / "global.base_prompt.md").write_text("项目版", encoding="utf-8")
    assert l.get("global.base_prompt") == "项目版"


def test_render_substitutes_vars(loader):
    l, _, _ = loader
    (proj := loader[2] / ".harness" / "prompts").mkdir(exist_ok=True)
    (proj / "t.md").write_text("写{{genre}}小说，{{target}}字。", encoding="utf-8")
    assert l.render("t", genre="仙侠", target="3000") == "写仙侠小说，3000字。"


def test_missing_var_raises(loader):
    l, _, proj = loader
    (proj / ".harness" / "prompts" / "t.md").write_text("{{genre}}/{{tone}}", encoding="utf-8")
    with pytest.raises(PromptRenderError) as ei:
        l.render("t", genre="仙侠")
    assert "tone" in str(ei.value)


def test_blocks_appended_after_render(loader):
    l, _, proj = loader
    (proj / ".harness" / "prompts" / "t.md").write_text("正文规则", encoding="utf-8")
    out = l.render_with_blocks("t", {}, ["【代码生成块】每章 3000 字"])
    assert out.index("正文规则") < out.index("【代码生成块】")


# ------------------------------------------------------------------ 输出契约插位（P9）

def test_insert_before_output_section():
    """块插到【输出格式】**之前**（输出契约是模型最后读的指令）。"""
    rendered = "要求：\n① 做点事\n\n【输出格式】严格 JSON：\n{}"
    out = insert_before_output_section(rendered, "\n【标准】\n（甲）")
    assert out.index("【标准】") < out.index("【输出格式】")
    assert "要求：\n① 做点事" in out
    assert "【输出格式】严格 JSON：\n{}" in out


def test_insert_without_output_section_appends():
    """模板没有输出契约段 → 安全降级为追加末尾（不抛错）。"""
    rendered = "只有正文"
    out = insert_before_output_section(rendered, "块")
    assert out == "只有正文\n块"


def test_insert_empty_block_is_noop():
    """空块 → 原样返回（字节级零变化）。"""
    rendered = "【输出格式】{}"
    assert insert_before_output_section(rendered, "") == rendered
