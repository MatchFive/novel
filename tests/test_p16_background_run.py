"""P16：非 TTY 下 run 走**后台线程** → 同一会话的 ``/answer`` 能应答审批。

**为什么**（2026-11-17 合并复审发现，列在「留到下期」）：
非 TTY 时 run **同步阻塞**在审批里 → REPL 读不到下一行 →
``printf '/task …\\n/answer 1\\n' | harness <项目>`` 的第二行**永不执行** → 挂死。
→ 非 TTY 下 run 走后台线程，REPL 继续读命令。

**TTY 为什么不这么做**：TTY 的审批走**终端选项卡**，由 run 自己读 stdin → REPL 与 run
**共享 stdin** → 两个读者会互相抢输入 → TTY 仍**阻塞**（不变）。
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

from harness.projects import create_project
from harness.repl import _join_background
from harness.session import Components, RunSession
from harness.store.project import ProjectStore


class _PipeStdin(io.StringIO):
    """带 isatty()=False 的 stdin 桩（非 TTY）。"""

    def isatty(self) -> bool:
        return False


def _session(tmp_path, name="非TTY书"):
    info = create_project(tmp_path, name)
    store = ProjectStore(info.path)
    from harness.config import ProviderConfig, Settings

    settings = Settings(library_root=Path("x"),
                        providers={"test": ProviderConfig(base_url="https://api.test",
                                                          api_key="k", lite="m",
                                                          standard="m", pro="m")},
                        default_provider="test", default_tier="standard",
                        web_verify=True)
    return RunSession(info, store, settings), store


def test_p16_non_tty_run_is_backgrounded(tmp_path, monkeypatch):
    """非 TTY 下：``_handle_brief`` **立即返回**（run 在后台），不阻塞命令循环。"""
    from harness.repl import _submit_brief

    sess, _store = _session(tmp_path)
    started = threading.Event()

    async def fake_run_agent(session, state, comp, *, intake=None, on_note=None):
        started.set()

    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(object(), None, None))
    monkeypatch.setattr("harness.repl.drive_run", fake_run_agent)
    monkeypatch.setattr("sys.stdin", _PipeStdin(""))

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)

    # run 已在后台启动（不阻塞）
    assert started.wait(timeout=2)
    _join_background()


def test_p16_tty_run_stays_blocking(tmp_path, monkeypatch):
    """TTY 下：**仍然阻塞**（run 与 REPL 共享 stdin → 两个读者会抢输入）。

    断言「`_handle_brief` **返回时** run 已经跑完」——变异成「TTY 也走后台」时，
    返回时 run 还在跑 → 断言失败（不是靠「跑过了」这种两边都成立的弱断言）。
    """
    import asyncio
    import time

    from harness.repl import _submit_brief

    sess, _store = _session(tmp_path, "TTY书")
    finished: list[float] = []

    async def fake_run_agent(session, state, comp, *, intake=None, on_note=None):
        await asyncio.sleep(0.2)     # 模拟 run 跑一会儿
        finished.append(time.monotonic())

    class _TtyStdin(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(object(), None, None))
    monkeypatch.setattr("harness.repl.drive_run", fake_run_agent)
    monkeypatch.setattr("sys.stdin", _TtyStdin(""))

    _submit_brief(sess, "写个仙侠", ["outline"], user_specified=False)
    returned_at = time.monotonic()

    assert finished, "run 还没跑完就返回了（TTY 下不该后台化）"
    assert finished[0] <= returned_at


def test_p16_answer_command_writes_pending_answer(tmp_path, monkeypatch):
    """**同一会话**里 ``/answer 1`` → 写 pending.answer（后台 run 的审批轮询能读到）。"""
    from harness.repl import _cmd_answer

    sess, store = _session(tmp_path, "应答书")
    base = store.root / ".harness"
    base.mkdir(parents=True, exist_ok=True)

    _cmd_answer(sess, "1")

    assert (base / "pending.answer").exists()
    assert (base / "pending.answer").read_text(encoding="utf-8").splitlines()[0] == "1"


def test_p16_repl_reads_commands_after_starting_run(tmp_path, monkeypatch):
    """**端到端**：非 TTY 会话里 ``/task …`` 之后，REPL **继续读**下一条命令（``/answer``）。

    这是 P16 的核心断言：以前 ``/task`` 会阻塞整个命令循环 → ``/answer`` 永远读不到。

    **REPL 跑在后台线程 + join 超时**：变异成「非 TTY 也阻塞」时，REPL 线程会卡在
    run 里（等一个永远不会出现的答案）→ join 超时 → **断言失败**（而不是把测试挂死）。
    """
    from harness.repl import run_repl

    sess, store = _session(tmp_path, "闭环书")
    started = threading.Event()
    released = threading.Event()

    async def fake_run_agent(session, state, comp, *, intake=None, on_note=None):
        started.set()
        # 模拟 run 阻塞在审批上：**一直**等 pending.answer 出现（正确实现下 REPL 会写它）
        while not (store.root / ".harness" / "pending.answer").exists():
            threading.Event().wait(0.01)
        released.set()

    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(object(), None, None))
    monkeypatch.setattr("harness.repl.drive_run", fake_run_agent)
    monkeypatch.setattr("sys.stdin", _PipeStdin("/task 写个仙侠\n/answer 1\n"))

    th = threading.Thread(
        target=run_repl, args=(sess.project, store), kwargs={"settings": sess.settings},
        daemon=True)
    th.start()
    th.join(timeout=10)

    assert not th.is_alive(), "REPL 没读完命令（/answer 不可达？——run 阻塞了命令循环）"
    assert started.is_set(), "run 没起来"
    assert released.is_set(), "run 没等到 pending.answer（同会话的 /answer 不可达？）"


def test_p16_eof_joins_background_run(tmp_path, monkeypatch):
    """EOF（stdin 读完）时**等后台 run 收尾**（不然后台 run 随进程退出被砍）。"""
    from harness.repl import run_repl

    sess, store = _session(tmp_path, "收尾书")
    finished = threading.Event()

    async def fake_run_agent(session, state, comp, *, intake=None, on_note=None):
        threading.Event().wait(0.5)     # 跑一会儿
        finished.set()

    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(object(), None, None))
    monkeypatch.setattr("harness.repl.drive_run", fake_run_agent)
    monkeypatch.setattr("sys.stdin", _PipeStdin("/task 写个仙侠\n"))

    run_repl(sess.project, store, settings=sess.settings)

    assert finished.is_set(), "EOF 时没等后台 run 收尾"
