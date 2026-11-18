"""P3 写作链七个提示词种子契约测试。

1. PromptLoader 七 key 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 七模板 {{var}} 集合与组装契约逐一相等（Task 5 渲染层消费，防拼写漂移；
   注释头里也只许出现契约变量）。
3. WRITING_ROUTING 七键值逐一钉死（路由表为共享生长表，P4 新增键由
   test_p4_prompts.py 钉值；不做全集相等）。
4. 路由注释头逐字（文件第一行，附录 B.4/B.7 惯例同 P2）。
"""
import re

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

PROMPT_KEYS = (
    "task.context_select",
    "task.draft_continue",
    "task.draft_review",
    "task.draft_revise",
    "task.draft_rewrite_beat",
    "task.draft_deaify",
    "task.draft_deaify_edits",
)

# Task 5 组装契约（brief「变量契约」节，与 prompts.json 实际 {{}} 集合逐一相等）
VAR_CONTRACT = {
    "task.context_select": {"objective", "beats", "target_beat", "catalog"},
    "task.draft_continue": {
        "style_guide", "generation_profile", "arc_outline", "chapter_objective",
        "pov_character", "remaining_beats", "retrieved_entries", "cast_cards",
        "pov_memories", "knowledge_state", "related_events", "open_promises",
        "rolling_summary", "recent_draft", "target_beat", "chapter_seq",
    },
    "task.draft_review": {
        "chapter_seq", "world_entries", "pov_memories", "knowledge_state",
        "cast_cards", "outline", "draft",
    },
    "task.draft_revise": {
        "issues", "flavor_hits", "outline", "world_entries", "cast_cards",
        "pov_memories", "knowledge_state", "draft",
    },
    "task.draft_rewrite_beat": {"outline", "prev_text", "beat_text", "next_text", "issues"},
    "task.draft_deaify": {"style_guide", "style_sample", "flavor_hits", "draft"},
    "task.draft_deaify_edits": {"style_guide", "style_sample", "targets"},
}

# 路由表钉死（brief 表；key → (tier 属性名, temperature, max_tokens)）
EXPECTED_ROUTING = {
    "task.context_select": ("lite", 0.2, 8192),
    "task.draft_continue": ("standard", 0.9, 8192),
    "task.draft_review": ("standard", 0.2, 8192),
    "task.draft_revise": ("standard", 0.7, 8192),
    "task.draft_rewrite_beat": ("standard", 0.6, 4096),
    "task.draft_deaify": ("standard", 0.5, 8192),
    "task.draft_deaify_edits": ("standard", 0.3, 4096),
}

# 路由注释头（文件第一行逐字，惯例同 P2 task.plot_units.md）
ROUTING_HEADER = {
    "task.context_select": "<!-- tier: lite | temperature: 0.2 | max_tokens: 8192 -->",
    "task.draft_continue": "<!-- tier: standard | temperature: 0.9 | max_tokens: 8192 -->",
    "task.draft_review": "<!-- tier: standard | temperature: 0.2 | max_tokens: 8192 -->",
    "task.draft_revise": "<!-- tier: standard | temperature: 0.7 | max_tokens: 8192 -->",
    "task.draft_rewrite_beat": "<!-- tier: standard | temperature: 0.6 | max_tokens: 4096 -->",
    "task.draft_deaify": "<!-- tier: standard | temperature: 0.5 | max_tokens: 8192 -->",
    "task.draft_deaify_edits": "<!-- tier: standard | temperature: 0.3 | max_tokens: 4096 -->",
}

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@pytest.fixture()
def loader(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(home_dir=home)


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_builtin_seed_loadable(loader, key):
    assert loader.get(key).strip()


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_var_set_matches_contract(loader, key):
    found = set(_VAR_RE.findall(loader.get(key)))
    assert found == VAR_CONTRACT[key], (
        f"{key} 变量漂移：多出 {sorted(found - VAR_CONTRACT[key])}，"
        f"缺失 {sorted(VAR_CONTRACT[key] - found)}"
    )


def test_writing_routing_pinned():
    """P3 七键逐值钉死。路由表是共享生长表（P4 起新增记忆键），故只钉本契约键集、
    不做全集相等——新增键由 tests/test_p4_prompts.py 各自钉值。"""
    for key, expected in EXPECTED_ROUTING.items():
        assert WRITING_ROUTING.get(key) == expected, \
            f"{key} 路由漂移：{WRITING_ROUTING.get(key)}"


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_routing_header_verbatim(loader, key):
    first_line = loader.get(key).splitlines()[0]
    assert first_line == ROUTING_HEADER[key]
