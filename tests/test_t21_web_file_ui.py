"""Task 21 验收 Critical ② 回归网：web 会话审批强制文件协议，与进程 stdin 解耦。

缺陷：服务端进程 stdin 是 TTY 时（Git Bash 终端里直接 ``harness web``），审批走了
终端路径读 stdin → EOF 秒取消；pending.md 根本不落地（Git Bash 里 ``< /dev/null``
仍 isatty=True）。验收 workaround 是 ``echo |`` 管道 stdin。

修法：RunSession 新增 ``force_file_ui=True``（web 会话显式传）——approval_ui 用
``non_tty_path`` 强制文件协议，审批路径选择不看 isatty；repl 默认不传，行为不变。
"""
import sys
import threading
import time

from harness.config import Settings
from harness.projects import create_project
from harness.run.approval import ApprovalCancelled
from harness.session import RunSession
from harness.store.project import ProjectStore


class _TtyStdin:
    """模拟 Git Bash 终端直连（harness web 起服务）的 TTY stdin。"""

    def isatty(self):
        return True


def _session(tmp_path, monkeypatch, *, force_file_ui):
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    info = create_project(tmp_path, "tty书")
    store = ProjectStore(info.path)
    settings = Settings(library_root=tmp_path, providers={}, default_provider="",
                        default_tier="standard", web_verify=True)
    return RunSession(info, store, settings, force_file_ui=force_file_ui), store


def test_session_force_file_ui_ignores_tty_stdin(tmp_path, monkeypatch):
    """isatty=True 的 stdin 下，force_file_ui 的会话审批仍走文件协议（pending.md 落地，
    绝不走终端 EOF 秒取消）——验收 Critical ②。"""
    sess, store = _session(tmp_path, monkeypatch, force_file_ui=True)
    ui = sess.approval_ui()
    assert ui.file_mode
    base = store.root / ".harness"
    outcome = {}

    def worker():
        try:
            ui.ask("是否确认当前规划并进入执行？", ["确认规划", "需要修改"])
        except ApprovalCancelled:
            outcome["cancelled"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        for _ in range(400):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        assert (base / "pending.md").exists()          # TTY stdin 下 pending.md 照样落地
        (base / "pending.answer").write_text("c\n", encoding="utf-8")
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert outcome.get("cancelled")                # 等到的是文件答案，不是终端 EOF
    finally:
        if thread.is_alive():                          # 防挂死：兜底给答案
            (base / "pending.answer").write_text("c\n", encoding="utf-8")
            thread.join(timeout=5)


def test_session_default_still_follows_stdin(tmp_path, monkeypatch):
    """默认（repl）不强制：TTY stdin 下仍走终端路径——repl 行为不变（A8 回归网）。"""
    sess, _store = _session(tmp_path, monkeypatch, force_file_ui=False)
    assert not sess.approval_ui().file_mode


def test_web_session_forces_file_ui(tmp_path, monkeypatch):
    """harness web 起的服务：会话层强制文件协议审批，与进程 stdin 是否 TTY 解耦。"""
    from fastapi.testclient import TestClient

    from harness.web.app import create_app

    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    settings = Settings(library_root=tmp_path / "novels", providers={}, default_provider="",
                        default_tier="standard", web_verify=True)
    client = TestClient(create_app(settings))
    pid = client.post("/api/projects", json={"title": "webtty"}).json()["id"]
    # 无 provider → 409；但 _session 已缓存——审批 UI 的强制文件协议可直查
    resp = client.post(f"/api/project/{pid}/task",
                       json={"raw": "写大纲", "deliverables": ["outline"]})
    assert resp.status_code == 409
    assert client.app.state.sessions[pid].approval_ui().file_mode
