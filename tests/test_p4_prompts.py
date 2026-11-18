"""P4 记忆链提示词种子契约测试（模式照 tests/test_p3_prompts.py）。

1. PromptLoader 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 模板 {{var}} 集合与组装契约双向相等（task.memory_extract：{cast_cards, chapter_content}；
   task.digest_compress（本地新增，seed 无）：{segments, vol}）。
3. WRITING_ROUTING 新键值钉死（memory_extract=standard/0.3/4096；
   digest_compress=lite/0.3/2048，裁定 6）+ 与 P3 七键并存。
4. 路由注释头逐字（文件第一行）。
5. 模板正文钉死输出格式——memory_extract 五键 / digest_compress 压缩段落列表
   （"segments"）——防种子漂移。
"""
import re

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

PROMPT_KEYS = ("task.memory_extract", "task.digest_compress")

# 组装契约（brief「变量契约」节，与模板实际 {{}} 集合逐一相等）
VAR_CONTRACT = {
    "task.memory_extract": {"cast_cards", "chapter_content"},
    "task.digest_compress": {"segments", "vol"},
}

# 路由表钉死（brief 裁定 6：key → (tier 属性名, temperature, max_tokens)）
EXPECTED_ROUTING = {
    "task.memory_extract": ("standard", 0.3, 4096),
    "task.digest_compress": ("lite", 0.3, 2048),
}

# 路由注释头（文件第一行逐字，惯例同 P3）
ROUTING_HEADER = {
    "task.memory_extract": "<!-- tier: standard | temperature: 0.3 | max_tokens: 4096 -->",
    "task.digest_compress": "<!-- tier: lite | temperature: 0.3 | max_tokens: 2048 -->",
}

# 五键输出格式钉死（模板【输出格式】节必须含有的顶层键）
EXTRACT_KEYS = ("events", "character_events", "info_updates", "promise_updates",
                "state_updates")

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
    assert len(WRITING_ROUTING) == 19,        f"路由表键数漂移（陌生键失防）：{sorted(WRITING_ROUTING)}"
    for key, expected in EXPECTED_ROUTING.items():
        assert WRITING_ROUTING.get(key) == expected, \
            f"{key} 路由漂移：{WRITING_ROUTING.get(key)}"


def test_writing_routing_keeps_p3_keys():
    """路由表是共享生长表：P3 七键不因新增记忆键漂移。"""
    for key in ("task.context_select", "task.draft_continue", "task.draft_review",
                "task.draft_revise", "task.draft_rewrite_beat", "task.draft_deaify",
                "task.draft_deaify_edits"):
        assert key in WRITING_ROUTING


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_routing_header_verbatim(loader, key):
    first_line = loader.get(key).splitlines()[0]
    assert first_line == ROUTING_HEADER[key]


def test_extract_output_format_five_keys(loader):
    """memory_extract 模板【输出格式】节必须钉死五键（parse 侧契约，防种子漂移丢键）。"""
    text = loader.get("task.memory_extract")
    tail = text.split("【输出格式】", 1)[-1]
    for name in EXTRACT_KEYS:
        assert f'"{name}"' in tail, f"task.memory_extract 输出格式缺键 {name}"


def test_digest_output_format_segments(loader):
    """digest_compress 模板【输出格式】节必须钉死压缩段落列表（"segments"）。"""
    text = loader.get("task.digest_compress")
    tail = text.split("【输出格式】", 1)[-1]
    assert '"segments"' in tail
