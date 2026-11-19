"""P35 git 自动快照测试（设计 §2.2 拍板⑧）。

真跑 git（skipif 无 git）；快照是「后悔药点缀」，绝拖不垮主链路——
git 不可用/非仓库/无变更一律 None + log，不 raise。
"""
import shutil

import pytest

from harness.snapshot import auto_snapshot, ensure_repo, snapshot

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")


def _git(root, *args) -> str:
    import subprocess
    out = subprocess.run(["git", *args], check=False, cwd=root, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# ---------------------------------------------------------------- ensure_repo

def test_ensure_repo_creates_git_dir_and_gitignore(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert ensure_repo(root) is True
    assert (root / ".git").is_dir()
    assert (root / ".gitignore").read_text(encoding="utf-8") == ".harness/\n"


def test_ensure_repo_is_idempotent(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert ensure_repo(root) is True
    text = (root / ".gitignore").read_text(encoding="utf-8")
    (root / "notes.txt").write_text("x", encoding="utf-8")
    assert ensure_repo(root) is True
    # 幂等：不覆盖已有 .gitignore、不动内容
    assert (root / ".gitignore").read_text(encoding="utf-8") == text


def test_ensure_repo_failure_returns_false(tmp_path, monkeypatch):
    from harness import snapshot as snap
    monkeypatch.setattr(snap, "_git", lambda *a, **k: None)
    root = tmp_path / "proj"
    root.mkdir()
    assert ensure_repo(root) is False


def test_create_project_initializes_repo(tmp_path):
    from harness.projects import create_project

    info = create_project(tmp_path / "lib", "测试书")
    assert (info.path / ".git").is_dir()
    assert (info.path / ".gitignore").read_text(encoding="utf-8") == ".harness/\n"


def test_ensure_repo_sets_local_identity_fallback(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_repo(root)
    # 无全局 user.name/email 的机器上 commit 也能成（harness 自管仓库的本地兜底）
    assert _git(root, "config", "user.name") == "harness"
    assert _git(root, "config", "user.email") == "harness@local"


# ------------------------------------------------------------------- snapshot

def test_snapshot_commits_changes_and_excludes_harness_dir(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_repo(root)
    (root / "world.md").write_text("# 世界观\n", encoding="utf-8")
    (root / ".harness").mkdir()
    (root / ".harness" / "events.log").write_text("noise\n", encoding="utf-8")

    commit = snapshot(root, "harness 快照：测试")
    assert commit
    tracked = _git(root, "ls-files")
    assert "world.md" in tracked
    assert ".harness/" not in tracked


def test_snapshot_returns_none_when_no_changes(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ensure_repo(root)
    assert snapshot(root, "harness 快照：首") is not None
    # 连续无变更 → None（git commit 本身失败，静默降级）
    assert snapshot(root, "harness 快照：再") is None


def test_snapshot_returns_none_outside_repo(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    assert snapshot(root, "harness 快照：外") is None


def test_git_failure_never_raises(tmp_path, monkeypatch):
    from harness import snapshot as snap
    root = tmp_path / "proj"
    root.mkdir()
    ensure_repo(root)
    (root / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(snap, "_git", lambda *a, **k: None)
    assert snapshot(root, "harness 快照：x") is None
    assert auto_snapshot(root, "定稿") is None


# -------------------------------------------------------------- auto_snapshot

def test_auto_snapshot_deterministic_message(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "world.md").write_text("# w\n", encoding="utf-8")
    commit = auto_snapshot(root, "定稿", '第1章「开局」（3000 字）')
    assert commit
    assert _git(root, "log", "-1", "--pretty=%s") == "harness 快照：定稿（第1章「开局」（3000 字））"


def test_auto_snapshot_lazy_init_old_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "world.md").write_text("# w\n", encoding="utf-8")
    commit = auto_snapshot(root, "审批通过", "规划确认门")
    assert commit
    assert (root / ".git").is_dir()
    assert _git(root, "log", "-1", "--pretty=%s") == "harness 快照：审批通过（规划确认门）"


def test_auto_snapshot_without_detail_has_no_paren(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "world.md").write_text("# w\n", encoding="utf-8")
    auto_snapshot(root, "手动快照")
    assert _git(root, "log", "-1", "--pretty=%s") == "harness 快照：手动快照"
