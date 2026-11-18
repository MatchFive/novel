"""P10 需求解析提示词契约测试（模式照 tests/test_p6_prompts.py）。

1. PromptLoader 可取（builtin 兜底，home 指向空目录防真实用户目录覆盖）。
2. 模板 {{var}} 集合 = {message, project_state}（与组装契约相等）。
3. WRITING_ROUTING 新键值钉死（standard/0.3/2048）+ 键数 == 19。
4. 路由注释头逐字（文件第一行）。
5. 模板正文（**去掉首行**）与 seed 底本**逐字节相等**——移植只允许加首行路由头，
   正文一个字都不许改（连「文件末尾无换行」这类细节也按字节比）。
"""
import re
import warnings
from importlib import resources
from pathlib import Path

import pytest

from harness.prompts.loader import PromptLoader
from harness.writing.pure import WRITING_ROUTING

PROMPT_KEYS = ("task.task_intake",)

VAR_CONTRACT = {
    "task.task_intake": {"message", "project_state"},
}

EXPECTED_ROUTING = {
    "task.task_intake": ("standard", 0.3, 2048),
}

ROUTING_HEADER = {
    "task.task_intake": "<!-- tier: standard | temperature: 0.3 | max_tokens: 2048 -->",
}

# seed 底本：**仓内副本**（`tests/fixtures/seed/`）——契约 1 的核心测试**不能**因为
# 「文档仓不可达」而静默变成空测（独立复审 B5：换台机器/换个 clone，比对测试直接跳过，
# pytest 默认不把 warning 当错误 → 逐字移植这条纪律就没人守了）。
# 文档仓那份（`intake.seed.md`）是**存档**，下面另有一条交叉核对。
REPO_SEED_DIR = Path(__file__).resolve().parent / "fixtures" / "seed"
REPO_SEED_FILES = {"task.task_intake": "task.task_intake.md"}

# 文档仓底本（存档位置）；不在时只跳过**交叉核对**，不影响仓内比对。
SEED_DIR = Path("d:/project/local_dream/.superpowers/sdd/2026-11-15-p10-brief-intake")
SEED_FILES = {"task.task_intake": "intake.seed.md"}

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
    """行尾归一（CRLF→LF）——**已不需要**：仓有 ``.gitattributes``（``*.md text eol=lf``，
    2026-11-17 P19），仓内 .md 一律 LF 入库/落盘 → 比对可以**逐字节**做。

    保留本函数只为让「曾经靠它绕开行尾漂移」这件事留在代码里可见（别再拿它当遮羞布）。
    """
    return data.replace(b"\r\n", b"\n")


def _seed_bytes(key: str) -> bytes | None:
    """读**仓内** seed 底本原始字节（缺失 → 直接失败，不静默跳过）。"""
    path = REPO_SEED_DIR / REPO_SEED_FILES[key]
    assert path.is_file(), f"仓内 seed 底本缺失：{path}（逐字比对不能静默跳过）"
    return path.read_bytes()


def _doc_seed_bytes(key: str) -> bytes | None:
    """读文档仓的 seed 底本（存档）；不在时 warn 并返回 None（只影响交叉核对）。"""
    path = SEED_DIR / SEED_FILES[key]
    if not path.is_file():
        warnings.warn(f"文档仓 seed 底本缺失，跳过交叉核对：{path}")
        return None
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
def test_body_matches_seed_bytes(key):
    """模板正文（去掉首行路由头）与 **仓内** seed 底本**逐字节相等**。

    仓内副本保证这条测试**永远是真的**（独立复审 B5：底本只在文档仓时，换台机器
    就静默跳过 → 逐字移植没人守）。文档仓那份另有一条交叉核对。

    **逐字节＝真逐字节**（2026-11-17 P19）：仓有 ``.gitattributes`` 的
    ``*.md text eol=lf`` → 仓内 .md 一律 LF → 这里**不再做行尾归一**
    （此前用 ``_lf()`` 归一，等于把「工作树被 autocrlf 写成 CRLF」这件事藏起来）。
    """
    seed = _seed_bytes(key)
    body = _strip_first_line(_builtin_bytes(key))
    assert body == seed, "移植只允许加首行路由头，正文一个字都不许改"


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_repo_seed_matches_doc_repo_seed(key):
    """仓内副本与文档仓存档**逐字节相等**（两份不能漂移）。"""
    doc = _doc_seed_bytes(key)
    if doc is None:
        return
    assert _seed_bytes(key) == doc
