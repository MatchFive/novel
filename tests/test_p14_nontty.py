"""P14：非 TTY 无人值守路径（设计 §2.1「非 TTY 退化为 `.harness/pending.md` 文件确认」）。

**之前**：REPL 在非 TTY 下直接 return None → agent loop **不可达** → 文件协议
（``ApprovalUI`` 早就实现了）**没有 agent run 会用到它** → 设计里这一块从未真正生效。

**现在**：
1. 非 TTY 会话**也能跑**员工主循环；
2. 审批在非 TTY 下走文件协议，pending 文件落在**项目根**的 ``.harness/``
   （与 REPL ``/answer`` 写 pending.answer 的位置**一致**——否则两边分叉，
   run 永远等不到答案 → 挂死）。
"""
from __future__ import annotations

from pathlib import Path

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.run.approval import ApprovalUI, Choice
from harness.store.project import ProjectStore


def _settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="standard-model",
                                          pro="pro-model")},
        default_provider="test", default_tier="standard", web_verify=True)


# ------------------------------------------------------------------ 文件落点

def test_approval_file_base_is_project_root(tmp_path, monkeypatch):
    """文件模式的 pending 落**指定目录**（项目根），不落 cwd。

    回归点：``/answer`` 写的是 ``sess.store.root/.harness/pending.answer``；
    若审批侧落 cwd → 两边分叉 → run 永远等不到答案（**挂死**，不是报错！）。

    **不真的等答案**（用 spy 顶掉轮询）：等不到答案时 ``wait_for_answer`` 是
    **无限**轮询 → 测试会挂住（变异检验时被这个挂死拖到超时）。
    同时 ``chdir`` 到 tmp：万一落错位置，也**不污染仓库**。
    """
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "项目" / ".harness"
    base.mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    class _SpyUI(ApprovalUI):
        def wait_for_answer(self, answer, *, count, multi, round=None):
            seen["answer_path"] = str(answer)
            seen["pending_exists"] = (base / "pending.md").exists()
            return Choice(indices=[0])

    ui = _SpyUI(stdin=_FakeStdin(tty=False), file_base=base, poll_interval=0)
    choice = ui.ask("要改吗？", ["改", "不改"])

    assert choice.indices == [0]
    assert seen["answer_path"].startswith(str(base)), "答案文件必须在项目根（与 /answer 同侧）"
    assert seen["pending_exists"] is True, "pending.md 必须落在项目根"
    # 读完后两个 pending 文件被清掉
    assert not (base / "pending.md").exists()
    assert not (base / "pending.answer").exists()


def test_approval_pending_is_written_to_file_base(tmp_path):
    """pending.md 落在 file_base 指定的目录。"""
    base = tmp_path / "proj" / ".harness"
    base.mkdir(parents=True)
    (base / "pending.answer").write_text("2\n", encoding="utf-8")

    seen: dict = {}

    class _SpyUI(ApprovalUI):
        def wait_for_answer(self, answer, *, count, multi, round=None):
            seen["pending_exists"] = (base / "pending.md").exists()
            seen["answer_path"] = str(answer)
            return Choice(indices=[1])

    ui = _SpyUI(stdin=_FakeStdin(tty=False), file_base=base, poll_interval=0)
    choice = ui.ask("要改吗？", ["改", "不改"])
    assert seen["pending_exists"] is True
    assert seen["answer_path"].startswith(str(base))
    assert choice.indices == [1]


class _FakeStdin:
    """带 isatty() 的 stdin 桩（控制 TTY 判定）。"""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        return ""


# ------------------------------------------------------------------ 非 TTY 能跑

async def test_non_tty_session_can_run_the_agent_loop(tmp_path, monkeypatch):
    """**非 TTY 会话也能跑员工主循环**（P14 核心）。

    之前 ``_make_components`` 在非 TTY 下直接返回 None → 员工主循环**不可达**，
    设计 §2.1 的「非 TTY 退化为 pending.md 文件确认」没有 agent run 会用到它。
    """
    from harness.session import RunSession

    info = create_project(tmp_path, "非TTY书")
    store = ProjectStore(info.path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    sess = RunSession(info, store, _settings())
    comp = sess.components()

    assert comp is not None, "非 TTY 下不该再拦在门外"
    llm, ui = comp.llm, comp.ui
    assert llm is not None and ui is not None


def test_non_tty_approval_uses_file_protocol(tmp_path, monkeypatch):
    """非 TTY 下审批**走文件协议**（不是终端选项卡）——设计 §2.1。

    用 spy 顶掉等待（不真的等答案）：等不到时 ``wait_for_answer`` 是**无限**轮询 →
    测试会**挂住**（变异检验时被这个挂死拖到超时）。
    """
    from harness.session import RunSession

    info = create_project(tmp_path, "非TTY审批书")
    store = ProjectStore(info.path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    sess = RunSession(info, store, _settings())
    ui = sess.components().ui

    seen: dict = {}

    class _SpyUI(type(ui)):
        def wait_for_answer(self, answer, *, count, multi, round=None):
            seen["answer_path"] = str(answer)
            return Choice(indices=[0])

    ui.__class__ = _SpyUI          # 就地换成 spy（不动生产代码）
    choice = ui.ask("要改吗？", ["改", "不改"])

    assert choice.indices == [0]
    # pending 落在**项目根**（与 /answer 同侧）
    assert seen["answer_path"].startswith(str(store.root / ".harness"))


def test_stub_notice_no_longer_requires_tty():
    """提示语**不再**说「需要交互式终端（TTY）」（现在只差 provider）。"""
    from harness import repl as repl_mod

    assert "TTY" not in repl_mod._STUB_NOTICE
