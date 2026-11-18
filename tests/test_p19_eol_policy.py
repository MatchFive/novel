"""P19：仓级行尾策略（`.gitattributes` 的 ``*.md text eol=lf``）——**守卫测试**。

**为什么**（P10 遗留、2026-11-17 修）：`core.autocrlf=true`（Windows 默认）把工作树
写成 CRLF →
① 仓内提示词模板与 **seed 底本逐字节不等**（移植纪律说「正文一个字都不许改」，
   行尾也算；此前比对测试用 `_lf()` 归一**绕开**了这件事 → 漂移被藏起来）；
② 同一份仓在 Windows / Linux 上落盘行尾不同 → 跨平台行为漂移。

**测试的是什么**：
1. `.gitattributes` **声明**了 `*.md text eol=lf`（策略在版本控制里，不在某人机器上）；
2. 仓内**内置提示词模板**与**seed 底本**里**没有 CR 字节**（落盘即 LF）。
   —— 这一条会在「有人用会写 CRLF 的编辑器改了 .md 且没归一」时红；
   修法：把该文件行尾归一为 LF（提交前 `git add --renormalize .` 亦可）。
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GITATTRIBUTES = REPO / ".gitattributes"
SEED_DIR = Path(__file__).resolve().parent / "fixtures" / "seed"


def test_gitattributes_declares_md_lf():
    """行尾策略写在**版本控制**里（不是靠某台机器的 core.autocrlf）。"""
    assert GITATTRIBUTES.is_file(), "缺 .gitattributes——行尾策略无处可查"
    rules = [ln.strip() for ln in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert "*.md text eol=lf" in rules, f"缺 `*.md text eol=lf` 规则（现有：{rules}）"


def test_builtin_prompt_templates_have_no_cr():
    """内置提示词模板**落盘即 LF**（逐字移植的底本，CR 会让字节比对失真）。"""
    builtin = resources.files("harness.prompts").joinpath("builtin")
    offenders = []
    for entry in builtin.iterdir():
        if not entry.name.endswith(".md"):
            continue
        raw = entry.read_bytes()
        if b"\r" in raw:
            offenders.append(entry.name)
    assert not offenders, f"这些模板含 CR（行尾未归一）：{offenders}"


def test_seed_fixtures_have_no_cr():
    """仓内 seed 底本**落盘即 LF**（它是逐字比对的基准）。"""
    offenders = [p.name for p in SEED_DIR.glob("*.md") if b"\r" in p.read_bytes()]
    assert not offenders, f"这些底本含 CR（行尾未归一）：{offenders}"
