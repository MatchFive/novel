"""P2 规划链五个提示词种子契约测试。

1. PromptLoader 五 key 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 反套路关键词逐字锁定——移植 web_dream tests/test_plot_antitrope_prompts.py:35-45 的断言
   （卷级定调用词含「哑巴主角」，剧情描述层只锁抠字眼/预知/讲经；auto_plan_plots 另锁「未来视野」）。
3. 五模板 {{var}} 集合与 T4 组装契约逐一相等（防拼写漂移；注释头里也只许出现契约变量）。
4. 无负向告诫段落（白名单/拒绝由代码保证，提示词里不写）。
5. 路由注释头逐字（tier/temperature/max_tokens，附录 B.4/B.7）。
"""
import re

import pytest

from harness.prompts.loader import PromptLoader

PROMPT_KEYS = (
    "task.auto_plan_plots",
    "task.plot_units",
    "task.chapter_headers",
    "task.chapter_beats",
    "task.chapter_selfcheck",
)

# T4 组装契约（task-3-brief.md「变量契约」节，测试钉死、逐一相等）
VAR_CONTRACT = {
    "task.auto_plan_plots": {
        "instruction", "target_words", "total_chapters", "arc_chapters",
        "arc_title", "arc_goal", "arc_summary", "arc_conflict", "arc_resolution",
        "existing_characters", "existing_world", "scale_block", "research", "meme_bank",
    },
    "task.plot_units": {
        "arc_goal", "arc_conflict", "arc_summary", "plots", "prev_context",
        "next_context", "existing_chapters", "cast", "world_entries",
        "target_words", "arc_chapters", "min_units", "batch_note",
    },
    "task.chapter_headers": {"groups", "story_block"},
    "task.chapter_beats": {
        "plot_title", "plot_summary", "plot_conflict", "plot_resolution",
        "plot_beats", "chapter_seq", "chapter_title", "chapter_objective",
        "anchors_block", "all_chapters", "cast", "world_entries",
        "target_words", "story_block",
    },
    "task.chapter_selfcheck": {"plot_title", "chapters", "target_words"},
}

# 路由注释头（附录 B.4/B.7，逐字）
ROUTING_HEADER = {
    "task.auto_plan_plots": "<!-- tier: pro | temperature: 0.5 | max_tokens: 8192 -->",
    "task.plot_units": "<!-- tier: standard | temperature: 0.5 | max_tokens: 8192 -->",
    "task.chapter_headers": "<!-- tier: standard | temperature: 0.4 | max_tokens: 2048 -->",
    "task.chapter_beats": "<!-- tier: standard | temperature: 0.6 | max_tokens: 4096 -->",
    "task.chapter_selfcheck": "<!-- tier: standard | temperature: 0.7 | max_tokens: 8192 -->",
}

# 四类套路装置（web_dream test_plot_antitrope_prompts.py:14-15 同关键词表；
# 卷级定调用词是「哑巴主角」，剧情描述层用词是「哑巴」）
_TROPES = ("抠字眼", "预知", "讲经")
_TROPES_FULL = _TROPES + ("哑巴",)

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@pytest.fixture()
def loader(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(home_dir=home)


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_builtin_seed_loadable(loader, key):
    assert loader.get(key).strip()


@pytest.mark.parametrize("key,kws", [
    ("task.auto_plan_plots", _TROPES_FULL),
    ("task.plot_units", _TROPES),
])
def test_antitrope_keywords_verbatim(loader, key, kws):
    t = loader.get(key)
    for kw in kws:
        assert kw in t, f"{key} 缺反套路条款：{kw}"


def test_arc_level_prompt_locks_future_sight(loader):
    # 移植 web_dream「卷骨架/逐卷情节必须同时约束金手指不得预知」断言
    assert "未来视野" in loader.get("task.auto_plan_plots")


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_var_set_matches_contract(loader, key):
    found = set(_VAR_RE.findall(loader.get(key)))
    assert found == VAR_CONTRACT[key], (
        f"{key} 变量漂移：多出 {sorted(found - VAR_CONTRACT[key])}，"
        f"缺失 {sorted(VAR_CONTRACT[key] - found)}"
    )


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_no_negative_admonition_section(loader, key):
    t = loader.get(key)
    assert "负向告诫" not in t
    assert "【拒绝清单】" not in t


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_routing_header_verbatim(loader, key):
    first_line = loader.get(key).splitlines()[0]
    assert first_line == ROUTING_HEADER[key]
