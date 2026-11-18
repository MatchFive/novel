"""P15：非 TTY 审批闭环——`/answer` 写前校验 + 非法答案的反馈。

两个真缺口（评审指出）：
1. **`/answer` 不校验** → 写个越界答案进文件 → run 侧只会**继续等**（协议如此）→
   答的人以为答了、run 却没动 → **当场报错**，不写文件。
2. **run 侧对非法答案无反馈** → 一直等，答的人**看不到任何提示** →
   在 ``pending.md`` 末尾**追加**一行说明（为什么无效、该怎么写）。
"""
from __future__ import annotations

from pathlib import Path

from harness.run.approval import ApprovalUI
from harness.store.project import ProjectStore


class _FakeStdin:
    def __init__(self, tty: bool = False):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _pending(base: Path, options: list[str]) -> Path:
    """造一份 pending.md（形态同真实渲染）。"""
    base.mkdir(parents=True, exist_ok=True)
    lines = ["# 待审批", "", "要改吗？", "", "第一行填选项号，第二行可选备注。", ""]
    lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
    (base / "pending.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return base / "pending.md"


# ------------------------------------------------------------------ /answer 校验

def test_answer_rejects_out_of_range(tmp_path, capsys):
    """**越界的选项号** → **当场报错**，**不写** pending.answer。"""
    from harness.repl import _cmd_answer

    class _Sess:
        pass

    store = ProjectStore(tmp_path)
    sess = _Sess()
    sess.store = store
    base = store.root / ".harness"
    _pending(base, ["改", "不改"])

    _cmd_answer(sess, "5")

    out = capsys.readouterr().out
    assert "越界" in out
    assert not (base / "pending.answer").exists(), "非法答案不该写进文件（run 会一直等）"


def test_answer_accepts_valid_option(tmp_path, capsys):
    """合法选项号 → 正常写入。"""
    from harness.repl import _cmd_answer

    class _Sess:
        pass

    store = ProjectStore(tmp_path)
    sess = _Sess()
    sess.store = store
    base = store.root / ".harness"
    _pending(base, ["改", "不改"])

    _cmd_answer(sess, "2 备注:先看看")

    assert (base / "pending.answer").exists()
    text = (base / "pending.answer").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "2"
    assert "备注:先看看" in text


def test_answer_without_pending_does_not_validate(tmp_path, capsys):
    """没有 pending.md（还没问）→ 不校验，照写（可能稍后才被读到）。"""
    from harness.repl import _cmd_answer

    class _Sess:
        pass

    store = ProjectStore(tmp_path)
    sess = _Sess()
    sess.store = store

    _cmd_answer(sess, "3")

    assert (store.root / ".harness" / "pending.answer").exists()


# ------------------------------------------------------------------ 非法答案的反馈

def test_invalid_answer_gets_hint_in_pending(tmp_path):
    """run 侧读到**非法答案** → 在 pending.md 追加「为什么无效」的提示。

    注意：真实路径下**非法答案不会让等待结束**（继续轮询）→ pending.md **留着**，
    答的人下一次看 pending.md 就能看见提示。（答案被**接受**时两个文件才被清掉。）
    """
    base = tmp_path / ".harness"
    pending = _pending(base, ["改", "不改"])
    answer = base / "pending.answer"
    answer.write_text("9\n", encoding="utf-8")          # 越界

    ui = ApprovalUI(stdin=_FakeStdin(tty=False), file_base=base, poll_interval=0)
    ui._note_invalid_answer(answer, "9", count=2, multi=False)

    text = pending.read_text(encoding="utf-8")
    assert "上一条答案**无效**" in text
    assert "越界" in text
    assert "pending.answer" in text      # 告诉他该改哪个文件


def test_wait_for_answer_notes_invalid_answer(tmp_path):
    """``wait_for_answer`` 读到非法答案时**会**提示（不是静默继续等）。

    **时间有界**：等待跑在后台线程里，主线程最多等 2 秒；无提示 → 断言失败（**不挂死**）。
    （变异检验时「不提示」这个变异的失败形态就是挂死——等待永不结束。
    → 这里用一个后台线程 + 超时把它变成**断言失败**。）
    """
    import threading
    import time

    base = tmp_path / ".harness"
    _pending(base, ["改", "不改"])
    answer = base / "pending.answer"
    answer.write_text("9\n", encoding="utf-8")      # 越界 → 一直等

    ui = ApprovalUI(stdin=_FakeStdin(tty=False), file_base=base, poll_interval=0.01)
    done = threading.Event()

    def _wait():
        try:
            ui.wait_for_answer(answer, count=2, multi=False)
        except (OSError, ValueError):   # pragma: no cover - 线程里兜住即可
            pass
        finally:
            done.set()

    th = threading.Thread(target=_wait, daemon=True)
    th.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not done.is_set():
        time.sleep(0.02)
    # 无提示 → 失败（而不是挂死）
    assert "上一条答案**无效**" in (base / "pending.md").read_text(encoding="utf-8")
    # 解开等待（写一个合法答案）→ 线程收尾
    answer.write_text("1\n", encoding="utf-8")
    th.join(timeout=2)
