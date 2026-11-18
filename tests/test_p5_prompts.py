"""P5 一致性审计提示词种子契约测试（模式照 tests/test_p4_prompts.py）。

1. PromptLoader 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 模板 {{var}} 集合与组装契约双向相等（task.consistency_audit 六变量）。
3. WRITING_ROUTING 新键值钉死（consistency_audit=standard/0.2/4096）。
4. 路由注释头逐字（文件第一行）。
5. 模板正文钉死输出格式——parse 侧契约四键 quote/type/issue/suggestion——防种子漂移。

P6 §1.5 验收链三模板（task.auto_plot_check / task.auto_global_check /
task.auto_editor_review）的变量集/路由/首行契约按共享生长表的惯例并入本文件；
其逐字移植契约（正文与 seed 底本逐字节相等）另见 tests/test_p6_prompts.py。
"""
import re

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

PROMPT_KEYS = ("task.consistency_audit", "task.consistency_fix",
               "task.web_search_verify", "task.web_search_followup",
               "task.auto_ref_check",
               "task.auto_plot_check", "task.auto_global_check",
               "task.auto_editor_review")

# 组装契约（brief「Produces」节，与模板实际 {{}} 集合逐一相等）
VAR_CONTRACT = {
    "task.consistency_audit": {"canonical_entries", "cast_cards", "character_memories",
                               "knowledge_state", "open_promises", "draft_text"},
    "task.consistency_fix": {"issue_title", "issue_detail", "quote", "context"},
    "task.web_search_verify": {"query", "results"},
    "task.web_search_followup": {"goal", "existing", "asked"},
    "task.auto_ref_check": {"premise", "constraints", "variation"},
    "task.auto_plot_check": {"chapter_summaries", "constraints", "goals",
                             "plot_plans", "scan_issues", "stage_checklist_items"},
    "task.auto_global_check": {"chapters", "constraints", "goals", "open_issues",
                               "open_promises", "premise", "samples",
                               "stage_checklist_items", "tree"},
    "task.auto_editor_review": {"chapters", "constraints", "goals", "open_issues",
                                "open_promises", "premise", "samples", "tree"},
}

# 路由表钉死（brief：key → (tier 属性名, temperature, max_tokens)）
EXPECTED_ROUTING = {
    "task.consistency_audit": ("standard", 0.2, 4096),
    "task.consistency_fix": ("standard", 0.3, 2048),
    "task.web_search_verify": ("standard", 0.2, 1024),
    "task.web_search_followup": ("standard", 0.2, 1024),
    "task.auto_ref_check": ("lite", 0.2, 4096),
    "task.auto_plot_check": ("pro", 0.2, 4096),
    "task.auto_global_check": ("pro", 0.2, 4096),
    "task.auto_editor_review": ("pro", 0.3, 4096),
}

# 路由注释头（文件第一行逐字，惯例同 P3/P4）
ROUTING_HEADER = {
    "task.consistency_audit": "<!-- tier: standard | temperature: 0.2 | max_tokens: 4096 -->",
    "task.consistency_fix": "<!-- tier: standard | temperature: 0.3 | max_tokens: 2048 -->",
    "task.web_search_verify": "<!-- tier: standard | temperature: 0.2 | max_tokens: 1024 -->",
    "task.web_search_followup": "<!-- tier: standard | temperature: 0.2 | max_tokens: 1024 -->",
    "task.auto_ref_check": "<!-- tier: lite | temperature: 0.2 | max_tokens: 4096 -->",
    "task.auto_plot_check": "<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->",
    "task.auto_global_check": "<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->",
    "task.auto_editor_review": "<!-- tier: pro | temperature: 0.3 | max_tokens: 4096 -->",
}

# 输出格式钉死（模板【输出格式】节必须含有的条目键，parse 侧契约）
ITEM_KEYS = ("quote", "type", "issue", "suggestion")

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


def test_render_six_vars(loader):
    """六变量同名传入严格渲染不缺（缺变量 PromptRenderError 即炸，无需反向用例）。"""
    text = loader.render("task.consistency_audit",
                         canonical_entries="铁律", cast_cards="卡",
                         character_memories="历", knowledge_state="知",
                         open_promises="诺", draft_text="文")
    assert "【铁律设定】铁律" in text
    assert "【待审正文】文" in text
    assert "{{" not in text


def test_writing_routing_pinned():
    assert len(WRITING_ROUTING) == 19,        f"路由表键数漂移（陌生键失防）：{sorted(WRITING_ROUTING)}"
    for key, expected in EXPECTED_ROUTING.items():
        assert WRITING_ROUTING.get(key) == expected, \
            f"{key} 路由漂移：{WRITING_ROUTING.get(key)}"


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_routing_header_verbatim(loader, key):
    first_line = loader.get(key).splitlines()[0]
    assert first_line == ROUTING_HEADER[key]


def test_output_format_item_keys(loader):
    """consistency_audit 模板【输出格式】节必须钉死 parse 侧四键（防种子漂移丢键）。"""
    text = loader.get("task.consistency_audit")
    tail = text.split("【输出格式】", 1)[-1]
    for name in ITEM_KEYS:
        assert f'"{name}"' in tail, f"task.consistency_audit 输出格式缺键 {name}"


def test_render_fix_four_vars(loader):
    """consistency_fix 四变量同名传入严格渲染不缺（缺变量 PromptRenderError 即炸）。"""
    text = loader.render("task.consistency_fix", issue_title="死角色晚出场",
                         issue_detail="甲在第 5 章后仍被提及", quote="夜风吹过山寨",
                         context="前文……")
    assert "【问题标题】死角色晚出场" in text
    assert "【待替换原文】夜风吹过山寨" in text
    assert "{{" not in text


def test_output_format_replacement_key(loader):
    """consistency_fix 模板【输出格式】节必须钉死 parse 侧单键 replacement。"""
    text = loader.get("task.consistency_fix")
    tail = text.split("【输出格式】", 1)[-1]
    assert '"replacement"' in tail, "task.consistency_fix 输出格式缺键 replacement"


def test_render_web_search_verify_vars(loader):
    """web_search_verify 两变量渲染不缺（query/results）。"""
    text = loader.render("task.web_search_verify", query="率土之滨 内政", results="[站] 标题")
    assert "【待核实】率土之滨 内政" in text
    assert "[站] 标题" in text
    assert "{{" not in text


def test_render_web_search_followup_vars(loader):
    """web_search_followup 三变量渲染不缺（goal/existing/asked）。"""
    text = loader.render("task.web_search_followup", goal="核实内政机制",
                         existing="已有结论", asked="率土之滨")
    assert "【本次目标】核实内政机制" in text
    assert "已有结论" in text
    assert "率土之滨" in text
    assert "{{" not in text


def test_render_auto_ref_check_vars(loader):
    """auto_ref_check 三变量渲染不缺（premise/constraints/variation）。"""
    text = loader.render("task.auto_ref_check", premise="剑与魔法西幻",
                         constraints="- 剑与魔法", variation="")
    assert "【需求】剑与魔法西幻" in text
    assert "【限制】" in text
    assert "{{" not in text


def test_output_format_refs_key(loader):
    """auto_ref_check 模板【输出格式】节必须钉死 parse 侧 refs 键。"""
    text = loader.get("task.auto_ref_check")
    tail = text.split("【输出格式】", 1)[-1]
    assert '"refs"' in tail
