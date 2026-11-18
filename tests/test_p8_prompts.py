"""P8 按章聚合修订提示词契约测试（模式照 tests/test_p6_prompts.py）。

1. PromptLoader 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 模板 {{var}} 集合与组装契约相等（`task.check_fix_chapter` 三变量）。
3. WRITING_ROUTING 新键值钉死（standard/0.3/4096）+ 键数 == 18。
4. 路由注释头逐字（文件第一行）。

**不做 seed 逐字比对**：`task.check_fix_chapter` 是本地自撰模板（seed 无等价模板）——
没有底本可比，比的是**变量集 + 路由 + 路由头 + 可渲染**。
"""
import re

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

PROMPT_KEY = "task.check_fix_chapter"

# 组装契约（与模板实际 {{}} 集合逐一相等：多出/缺失都要炸）
VAR_CONTRACT = {"chapter_title", "chapter_body", "issues"}

# 路由表钉死（key → (tier 属性名, temperature, max_tokens)）
EXPECTED_ROUTING = {PROMPT_KEY: ("standard", 0.3, 4096)}

# 路由注释头（文件第一行逐字，惯例同 P3/P4/P5/P6）
ROUTING_HEADER = {
    PROMPT_KEY: "<!-- tier: standard | temperature: 0.3 | max_tokens: 4096 -->",
}

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@pytest.fixture()
def loader(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(home_dir=home)


def test_builtin_loadable(loader):
    assert loader.get(PROMPT_KEY).strip()


def test_var_set_matches_contract(loader):
    found = set(_VAR_RE.findall(loader.get(PROMPT_KEY)))
    assert found == VAR_CONTRACT, (
        f"{PROMPT_KEY} 变量漂移：多出 {sorted(found - VAR_CONTRACT)}，"
        f"缺失 {sorted(VAR_CONTRACT - found)}"
    )


def test_routing_header_verbatim(loader):
    first_line = loader.get(PROMPT_KEY).splitlines()[0]
    assert first_line == ROUTING_HEADER[PROMPT_KEY]


def test_routing_pinned():
    assert len(WRITING_ROUTING) == 19, f"路由表键数漂移（陌生键失防）：{sorted(WRITING_ROUTING)}"
    for key, expected in EXPECTED_ROUTING.items():
        assert WRITING_ROUTING.get(key) == expected, \
            f"{key} 路由漂移：{WRITING_ROUTING.get(key)}"


def test_render_all_contract_vars(loader):
    """按契约传入全部变量能严格渲染（缺变量 PromptRenderError 即炸，无需反向用例）。"""
    text = loader.render(PROMPT_KEY, **{name: f"<{name}>" for name in VAR_CONTRACT})
    assert "{{" not in text


def test_template_asks_for_id_not_quote(loader):
    """模板要求模型回**意见 id**（定位由代码用我们自己的 quote 做，不靠模型复述引文）。"""
    text = loader.get(PROMPT_KEY)
    assert '"id"' in text
    assert '"replacement"' in text
