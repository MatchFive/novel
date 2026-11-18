"""P6 验收链提示词种子契约测试（模式照 tests/test_p5_prompts.py）。

1. PromptLoader 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 模板 {{var}} 集合与组装契约双向相等（plot_check 六变量 / global_check 九变量 /
   editor_review 八变量）。
3. WRITING_ROUTING 新键值钉死（pro/0.2/4096、pro/0.2/4096、pro/0.3/4096）+ 键数 == 17。
4. 路由注释头逐字（文件第一行）。
5. 模板正文（**去掉首行**）与 seed 底本**逐字节相等**——移植只允许加首行路由头，
   正文一个字都不许改（连「文件末尾无换行」这类细节也按字节比）。
"""
import re
from importlib import resources
from pathlib import Path

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "seed" / "p6"

PROMPT_KEYS = ("task.auto_plot_check", "task.auto_global_check", "task.auto_editor_review")

# 组装契约（brief「契约」节，与模板实际 {{}} 集合逐一相等：多出/缺失都要炸）
VAR_CONTRACT = {
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
    "task.auto_plot_check": ("pro", 0.2, 4096),
    "task.auto_global_check": ("pro", 0.2, 4096),
    "task.auto_editor_review": ("pro", 0.3, 4096),
}

# 路由注释头（文件第一行逐字，惯例同 P3/P4/P5）
ROUTING_HEADER = {
    "task.auto_plot_check": "<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->",
    "task.auto_global_check": "<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->",
    "task.auto_editor_review": "<!-- tier: pro | temperature: 0.3 | max_tokens: 4096 -->",
}

# seed 底本（**仓内 fixtures**，P22/B1：不再依赖文档仓绝对路径——跨机器时
# warn-skip 会让「逐字移植」静默失守）。fixtures 缺失 = 测试硬失败。
SEED_DIR = FIXTURE_DIR
SEED_FILES = {
    "task.auto_plot_check": "seed-ref.auto_plot_check.md",
    "task.auto_global_check": "seed-ref.auto_global_check.md",
    "task.auto_editor_review": "seed-ref.auto_editor_review.md",
}

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@pytest.fixture()
def loader(tmp_path):
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)
    return PromptLoader(home_dir=home)


def _builtin_bytes(key: str) -> bytes:
    """内置模板文件的原始字节（不走 loader 的换行归一，保留末尾换行等细节）。"""
    return (resources.files("harness.prompts").joinpath("builtin")
            .joinpath(f"{key}.md").read_bytes())


def _strip_first_line(data: bytes) -> bytes:
    """去掉首行（路由头）连同其行尾，返回正文原始字节。"""
    _, sep, body = data.partition(b"\n")
    assert sep, "模板文件首行没有换行——路由头不在第一行"
    return body


def _lf(data: bytes) -> bytes:
    """行尾归一（CRLF→LF）。

    git 的 core.autocrlf 让同一个 blob 在不同平台落盘行尾不同（Windows CRLF / Linux LF），
    所以只比**内容**不比行尾风格；末尾有无换行仍被严格比较（换行符本身参与比较，
    seed 底本「文件末尾无换行」的细节照样钉死）。
    """
    return data.replace(b"\r\n", b"\n")


def _seed_bytes(key: str) -> bytes:
    """读 seed 底本原始字节；fixtures 缺失 = 硬失败（不再 warn-skip，P22/B1）。"""
    path = SEED_DIR / SEED_FILES[key]
    if not path.is_file():
        pytest.fail(f"seed 底本 fixtures 缺失（逐字比对失去依据）：{path}")
    return path.read_bytes()


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


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_routing_header_verbatim(loader, key):
    first_line = loader.get(key).splitlines()[0]
    assert first_line == ROUTING_HEADER[key]


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_render_all_contract_vars(loader, key):
    """按契约传入全部变量能严格渲染（缺变量 PromptRenderError 即炸，无需反向用例）。"""
    text = loader.render(key, **{name: f"<{name}>" for name in VAR_CONTRACT[key]})
    assert "{{" not in text


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_body_matches_seed_bytes(key):
    """正文（去掉首行路由头）与 seed 底本逐字节相等——逐字移植，一个字都不许改。"""
    seed = _seed_bytes(key)
    body = _strip_first_line(_builtin_bytes(key))
    assert _lf(body) == _lf(seed), (
        f"{key} 正文与 seed 底本不一致：移植时被改写/多字/少字/末尾换行不符"
    )


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_body_trailing_newline_matches_seed(key):
    """正文末尾有无换行与 seed 一致（seed 三份底本均「末尾无换行」）。"""
    seed = _seed_bytes(key)
    body = _strip_first_line(_builtin_bytes(key))
    assert body.endswith(b"\n") == seed.endswith(b"\n"), \
        f"{key} 正文末尾换行与 seed 不一致"
