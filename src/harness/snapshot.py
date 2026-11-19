"""git 自动快照（设计 §2.2 拍板⑧，P35）：项目文件夹即 git 仓库——章节定稿 / 审批通过 /
批量设定变更后自动 commit，git 管内容文件的多版本后悔药（与 events.log 的运行态
审计互补）。

**失败口径**：快照是点缀，**绝不拖垮主链路**——git 未装 / 非仓库 / 无变更 / 任何
subprocess 异常，一律 log + 返回 None。message 用确定性文案（拍板⑧的「生成式摘要」
本地不做：每章多一次 LLM 换一行提交摘要，收益不抵成本；reason/detail 已含定稿章号/
字数/文件数，后悔药定位足够）。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

__all__ = ["auto_snapshot", "ensure_repo", "snapshot"]

log = logging.getLogger("snapshot")

#: 照契约原文：整个 .harness/ 不进版本库（events.log/llm.log 体积大，审计留痕由它
#: 自身承担）。副作用如实记账：项目级 .harness/skills/ 也不进快照。
_GITIGNORE = ".harness/\n"


def _git(root: Path, *args: str) -> str | None:
    """git 命令 choke point：成功 stdout strip；任何失败（git 缺失/非仓库/参数错）→
    None + log.warning。绝不 raise。"""
    try:
        result = subprocess.run(["git", *args], check=False, cwd=Path(root), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git 快照不可用（%s %s）：%s", "git", " ".join(args), exc)
        return None
    if result.returncode != 0:
        log.warning("git %s 失败（%s）：%s", " ".join(args), root, result.stderr.strip()[:200])
        return None
    return result.stdout.strip()


def ensure_repo(root: Path) -> bool:
    """幂等初始化：git init（无仓库时）+ 写 .gitignore；返回是否有可用仓库。

    老项目首次 auto_snapshot 时惰性 init——create_project 也已挂上（新项目即仓库）。
    """
    root = Path(root)
    if _git(root, "rev-parse", "--is-inside-work-tree") is None and _git(root, "init") is None:
        return False
    ignore = root / ".gitignore"
    try:
        if not ignore.is_file():
            ignore.write_text(_GITIGNORE, encoding="utf-8", newline="\n")
    except OSError as exc:
        log.warning("写 .gitignore 失败（%s）：%s", ignore, exc)
    # 本地 identity 兜底：机器无全局 user.name/email 时 commit 也能成（harness 自管仓库）
    if _git(root, "config", "user.name") is None:
        _git(root, "config", "user.name", "harness")
    if _git(root, "config", "user.email") is None:
        _git(root, "config", "user.email", "harness@local")
    return True


def snapshot(root: Path, message: str) -> str | None:
    """``git add -A && git commit``；返回 commit hash；无变更/失败 → None。"""
    root = Path(root)
    if _git(root, "rev-parse", "--is-inside-work-tree") is None:
        return None
    _git(root, "add", "-A")
    out = _git(root, "commit", "-m", message, "--no-verify")
    if out is None:
        return None
    return _git(root, "rev-parse", "--short", "HEAD")


def auto_snapshot(root: Path, reason: str, detail: str = "") -> str | None:
    """挂点统一入口：ensure_repo（惰性 init）+ snapshot；message 确定性
    ``harness 快照：{reason}（{detail}）``。失败一律 None（调用方不感知）。"""
    root = Path(root)
    if not ensure_repo(root):
        return None
    message = f"harness 快照：{reason}" + (f"（{detail}）" if detail else "")
    return snapshot(root, message)
