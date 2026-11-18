import threading
import time

import pytest

from harness.run.approval import (
    ApprovalCancelled,
    ApprovalUI,
    Choice,
    _render_pending,
    confirm_destructive,
    parse_answer_line,
    read_pending_header,
    read_pending_round,
    render_card,
    terminal_ask,
)
from harness.store.project import ProjectStore


def _stamped(base, body: str) -> str:
    """协议 v2（B3）：答案须带 pending.md 头里的 round——run 只收本轮的。

    测试里模拟 repl /answer 的真实行为：从 pending.md 抄轮次盖进答案文件。
    """
    tok = read_pending_round((base / "pending.md").read_text(encoding="utf-8"))
    return f"round:{tok}\n{body}" if tok else body


class FakeStdin:
    """可注入的 TTY 判定桩：替代 sys.stdin.isatty() 的测试缝。"""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def make_fake_ask(return_choice=None):
    calls = []

    def fake(question, options, multi=False, note=None):
        calls.append({"question": question, "options": options, "multi": multi, "note": note})
        return return_choice or Choice(indices=[0])

    return fake, calls


# ---------- Choice 数据类 ----------


def test_choice_defaults():
    c = Choice(indices=[1, 2])
    assert c.indices == [1, 2]
    assert c.note is None


# ---------- 注入假 ask 的单元测试 ----------


def test_ask_uses_injected_fake_on_tty():
    fake, calls = make_fake_ask()
    ui = ApprovalUI(ask=fake, stdin=FakeStdin(True))
    c = ui.ask("继续?", ["好", "不"])
    assert c.indices == [0]
    assert calls == [{"question": "继续?", "options": ["好", "不"], "multi": False, "note": None}]


def test_ask_passes_multi_and_note_to_fake():
    fake, calls = make_fake_ask(return_choice=Choice(indices=[0, 1], note="备注"))
    ui = ApprovalUI(ask=fake, stdin=FakeStdin(True))
    ui.ask("选哪些?", ["甲", "乙"], multi=True, note="预置备注")
    assert calls[0]["multi"] is True
    assert calls[0]["note"] == "预置备注"


# ---------- 终端行解析与渲染 ----------


def test_render_card_shows_box_and_options():
    card = render_card("问题?", ["甲", "乙"], multi=False)
    assert "┌" in card and "└" in card
    assert "问题?" in card
    assert "1) 甲" in card and "2) 乙" in card


def test_parse_answer_line_basic_and_note():
    c = parse_answer_line("1 备注:原因", count=3, multi=False)
    assert c.indices == [0] and c.note == "原因"


def test_parse_answer_line_multi():
    c = parse_answer_line("1 3", count=3, multi=True)
    assert c.indices == [0, 2]


def test_parse_answer_line_rejects_out_of_range_and_multi_when_single():
    assert parse_answer_line("9", count=2, multi=False) is None
    assert parse_answer_line("1 2", count=3, multi=False) is None
    assert parse_answer_line("abc", count=3, multi=True) is None
    assert parse_answer_line("备注:只有备注", count=3, multi=True) is None


def test_terminal_ask_empty_twice_cancels():
    lines = iter(["", ""])
    with pytest.raises(ApprovalCancelled):
        terminal_ask("q", ["a"], inp=lambda prompt: next(lines), out=lambda s: None)


def test_terminal_ask_ctrl_c_cancels():
    def boom(prompt):
        raise KeyboardInterrupt

    with pytest.raises(ApprovalCancelled):
        terminal_ask("q", ["a"], inp=boom, out=lambda s: None)


def test_terminal_ask_eof_cancels():
    def boom(prompt):
        raise EOFError

    with pytest.raises(ApprovalCancelled):
        terminal_ask("q", ["a"], inp=boom, out=lambda s: None)


def test_terminal_ask_parses_choice_and_note():
    lines = iter(["2 备注:再想想"])
    c = terminal_ask("q", ["a", "b"], inp=lambda prompt: next(lines), out=lambda s: None)
    assert c.indices == [1] and c.note == "再想想"


# ---------- confirm_destructive ----------


def test_confirm_destructive_true_only_for_literal_confirm():
    seen = {}

    def fake(question, options, multi=False, note=None):
        seen["question"] = question
        seen["options"] = options
        return Choice(indices=[options.index("确认清空")])

    ui = ApprovalUI(ask=fake, stdin=FakeStdin(True))
    assert confirm_destructive(ui, "全部记忆") is True
    assert "确认清空" in seen["options"]
    assert "全部记忆" in seen["question"]


def test_confirm_destructive_false_on_cancel():
    fake, _ = make_fake_ask(return_choice=Choice(indices=[1]))
    ui = ApprovalUI(ask=fake, stdin=FakeStdin(True))
    assert confirm_destructive(ui, "全部记忆") is False


# ---------- 非 TTY 文件退化 ----------


def test_non_tty_path_forces_file_mode_and_cleans_up(tmp_path):
    base = tmp_path / ".harness"
    base.mkdir()
    answer = base / "pending.answer"

    def writer():
        # 与 repl /answer 真实时序一致：等 ask 落地 pending.md、waiter 进入轮询后再写答案
        # （终审 Finding ③ 起，ask 写 pending.md 前会先清掉**提前**写入的 pending.answer——
        # 那是上一轮残留的合法答案，不清会被本轮 ask 秒消费成错答）。
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        answer.write_text(_stamped(base, "2\n备注:换个思路\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(True))
    choice = ui.ask("选哪个?", ["甲", "乙", "丙"])
    t.join(timeout=5)
    assert not t.is_alive()
    assert choice.indices == [1]
    assert choice.note == "换个思路"
    assert not answer.exists()
    assert not (base / "pending.md").exists()


def test_file_mode_writes_pending_md_then_polls_answer(tmp_path):
    base = tmp_path / ".harness"
    base.mkdir()
    answer = base / "pending.answer"

    captured = {}

    def writer():
        time.sleep(0.05)  # 等 ask 进入轮询、pending.md 已落盘
        captured["pending"] = (base / "pending.md").read_text(encoding="utf-8")
        answer.write_text(_stamped(base, "1 3\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    choice = ui.ask("选?", ["a", "b", "c"], multi=True)
    t.join(timeout=5)
    assert not t.is_alive()
    assert choice.indices == [0, 2]
    pending = captured["pending"]
    assert "选?" in pending
    assert "1. a" in pending and "3. c" in pending


def test_file_mode_poll_skips_invalid_then_accepts_valid(tmp_path):
    base = tmp_path / ".harness"
    base.mkdir()
    answer = base / "pending.answer"

    def writer():
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        answer.write_text(_stamped(base, "99\n"), encoding="utf-8")  # 越界，轮询应忽略
        time.sleep(0.05)
        answer.write_text(_stamped(base, "2\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    choice = ui.ask("选?", ["a", "b"])
    t.join(timeout=5)
    assert not t.is_alive()
    assert choice.indices == [1]


def test_file_mode_cancel_marker_raises_and_cleans(tmp_path):
    base = tmp_path / ".harness"
    base.mkdir()

    def writer():
        # 同 test_non_tty_path_forces_file_mode_and_cleans_up：答案在 pending.md 落地后写
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        (base / "pending.answer").write_text(_stamped(base, "取消\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    with pytest.raises(ApprovalCancelled):
        ui.ask("q?", ["a"])
    t.join(timeout=5)
    assert not t.is_alive()
    assert not (base / "pending.md").exists()
    assert not (base / "pending.answer").exists()


def test_pending_md_hint_only_for_multi(tmp_path):
    """单选 pending.md 不写「可多选」提示（照写的用户在单选卡填 1 2 → 解析失败、轮询卡死）。"""
    from harness.run.approval import _render_pending

    single = _render_pending("q?", ["甲", "乙"], multi=False)
    multi = _render_pending("q?", ["甲", "乙"], multi=True)
    assert "可多选" not in single
    assert "可多选" in multi


def test_ask_ignores_stale_preexisting_answer_sync(tmp_path):
    """终审 Finding ③：上轮残留的合法格式 pending.answer 不得被本轮 ask 秒消费。

    场景：答案已写盘、waiter 尚未消费时进程被杀/重启 → 下一个 ask 若不清残留，
    会瞬间把旧答案当作对新问题的回答（静默错答）。修复：写 pending.md 前先删残留。
    """
    base = tmp_path / ".harness"
    base.mkdir()
    (base / "pending.answer").write_text("1\n", encoding="utf-8")  # 残留：合法格式、错的问题
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    result: dict = {}

    def asker():
        result["choice"] = ui.ask("新问题？", ["甲", "乙"])

    t = threading.Thread(target=asker)
    t.start()
    t.join(timeout=1)
    # 残留未被消费：ask 仍挂起等新答案（修复前：瞬间返回 [0]，线程已死）
    assert t.is_alive()
    assert "choice" not in result
    assert (base / "pending.md").exists()        # 问题还在等
    (base / "pending.answer").write_text(_stamped(base, "2\n"), encoding="utf-8")  # 本轮的真答案
    t.join(timeout=5)
    assert not t.is_alive()
    assert result["choice"].indices == [1]


def test_ask_ignores_stale_preexisting_answer_async(tmp_path):
    """异步文件协议同口径：残留 pending.answer 在写 pending.md 前被清掉。"""
    import asyncio

    base = tmp_path / ".harness"
    base.mkdir()
    (base / "pending.answer").write_text("1\n", encoding="utf-8")
    ui = ApprovalUI(non_tty_path=base, poll_interval=0.01, stdin=FakeStdin(False))

    async def main():
        ask_task = asyncio.create_task(ui.ask_async("新问题？", ["甲", "乙"]))
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            await asyncio.sleep(0.005)
        assert (base / "pending.md").exists()
        assert not ask_task.done()               # 残留未被秒消费
        (base / "pending.answer").write_text(_stamped(base, "2\n"), encoding="utf-8")
        choice = await asyncio.wait_for(ask_task, timeout=5)
        assert choice.indices == [1]

    asyncio.run(main())


@pytest.mark.parametrize("marker", ["CANCEL", "cancel", "Cancel"])
def test_cancel_marker_case_insensitive(tmp_path, marker):
    base = tmp_path / ".harness"
    base.mkdir()

    def writer():
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        (base / "pending.answer").write_text(_stamped(base, marker + "\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    with pytest.raises(ApprovalCancelled):
        ui.ask("q?", ["a"])
    t.join(timeout=5)
    assert not t.is_alive()


def test_non_tty_without_path_auto_degrades_to_cwd_dot_harness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def writer():
        for _ in range(200):
            if (tmp_path / ".harness" / "pending.md").exists():
                break
            time.sleep(0.005)
        d = tmp_path / ".harness"
        d.mkdir(exist_ok=True)
        (d / "pending.answer").write_text(_stamped(d, "1\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    ui = ApprovalUI(poll_interval=0, stdin=FakeStdin(False))
    choice = ui.ask("q?", ["a", "b"])
    t.join(timeout=5)
    assert not t.is_alive()
    assert choice.indices == [0]
    assert not (tmp_path / ".harness" / "pending.md").exists()
    assert not (tmp_path / ".harness" / "pending.answer").exists()


def test_tty_stdin_without_path_uses_terminal_branch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake, calls = make_fake_ask()
    ui = ApprovalUI(ask=fake, poll_interval=0, stdin=FakeStdin(True))
    ui.ask("q?", ["a"])
    assert calls[0]["question"] == "q?"
    assert not (tmp_path / ".harness").exists()  # 没有误退化到文件模式


def test_file_mode_default_base_anchored_absolute_ignores_later_chdir(tmp_path, monkeypatch):
    """Minor 2 后半：文件模式默认 .harness 在构造时锚定为绝对路径——构造后 chdir 不拆散写入与轮询。"""
    proj = tmp_path / "proj"
    base = proj / ".harness"
    base.mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(proj)
    ui = ApprovalUI(poll_interval=0)          # 锚定 proj/.harness（cwd 解析一次固定）
    monkeypatch.chdir(other)                  # 构造后切走 cwd：相对路径在这里就会分叉

    def writer():
        # 与 repl /answer 真实时序一致：等 pending.md 落地后再写答案
        # （终审 Finding ③ 起，ask 会先清掉**提前**写入的 pending.answer）。
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        (base / "pending.answer").write_text(_stamped(base, "1\n"), encoding="utf-8")

    t = threading.Thread(target=writer)
    t.start()
    choice = ui.ask("问题？", ["甲", "乙"], multi=False)
    t.join(timeout=5)
    assert not t.is_alive()
    assert choice.indices == [0]              # 轮询读到了锚定目录里的答案 = 写入与轮询相遇
    assert not (base / "pending.answer").exists()  # 答案文件被消费（确证读的正是锚定路径）


# ---------- write_answer / cancel_answer（Task 11：repl 与 web 共享写盘） ----------


def _write_pending(base, question="改吗？", options=("改", "不改"), multi=False):
    """造一份与真实渲染同形的 pending.md（协议 v2：机器头带 round）→ 返回轮次。"""
    import secrets

    base.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_hex(8)
    (base / "pending.md").write_text(
        _render_pending(question, list(options), multi, round=tok), encoding="utf-8")
    return tok


# ---------- B3：迟到的 /answer 轮次竞态（协议 v2：答案带轮次，run 只收本轮的） ----------

ROUND_A, ROUND_B = "a" * 16, "b" * 16


def _pending_with_round(tmp_path, round_token=ROUND_A):
    base = tmp_path / ".harness"
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending.md").write_text(
        _render_pending("改吗？", ["改", "不改"], multi=False, round=round_token),
        encoding="utf-8")
    return base


def test_pending_header_carries_round():
    """_render_pending(round=…)：机器头带轮次；read_pending_round 取回；旧格式 → None。"""
    text = _render_pending("q", ["a", "b"], multi=False, round=ROUND_A)
    assert f"round={ROUND_A}" in text
    assert read_pending_header(text) == (2, False)      # 旧解析口径不变
    assert read_pending_round(text) == ROUND_A
    legacy = _render_pending("q", ["a", "b"], multi=False)
    assert read_pending_round(legacy) is None           # 旧格式无轮次


def test_try_read_answer_accepts_current_round(tmp_path):
    ui = ApprovalUI(poll_interval=0)
    base = _pending_with_round(tmp_path)
    (base / "pending.answer").write_text(f"round:{ROUND_A}\n2 备注:再想想\n",
                                         encoding="utf-8")
    choice = ui._try_read_answer(base / "pending.answer", count=2, multi=False,
                                 round=ROUND_A)
    assert choice is not None and choice.indices == [1] and choice.note == "再想想"


@pytest.mark.parametrize("stale", [None, ROUND_B])
def test_try_read_answer_rejects_stale_round_and_notes(tmp_path, stale):
    """无轮次（旧格式/迟到回落路径）或别轮次 → **永不消费** + pending.md 里有说明（B3）。

    拒收**不删文件**（非破坏；留给写入方看见，finally 统一清理）。
    """
    ui = ApprovalUI(poll_interval=0)
    base = _pending_with_round(tmp_path)
    body = "1\n" if stale is None else f"round:{stale}\n1\n"
    (base / "pending.answer").write_text(body, encoding="utf-8")
    choice = ui._try_read_answer(base / "pending.answer", count=2, multi=False,
                                 round=ROUND_A)
    assert choice is None
    text = (base / "pending.md").read_text(encoding="utf-8")
    assert "无效" in text and "轮次" in text
    assert (base / "pending.answer").exists()


def test_try_read_answer_round_none_keeps_legacy_acceptance(tmp_path):
    """round=None（旧调用方/测试直调）→ 不校验轮次，裸答案照常收。"""
    ui = ApprovalUI(poll_interval=0)
    base = _pending_with_round(tmp_path)
    (base / "pending.answer").write_text("1\n", encoding="utf-8")
    choice = ui._try_read_answer(base / "pending.answer", count=2, multi=False)
    assert choice is not None and choice.indices == [0]


def test_b3_answer_from_previous_round_not_consumed_end_to_end(tmp_path):
    """端到端 B3：第 1 轮答完 → 第 2 轮挂起；**带第 1 轮轮次**的迟到答案不得被收
    （用户在第 1 轮结束后才敲的 /answer）；带本轮轮次的真答案照收。"""
    base = tmp_path / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    rounds: dict = {}

    def writer1():  # 第 1 轮：正常答掉，顺手记下它的轮次
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            time.sleep(0.005)
        rounds["r1"] = read_pending_round((base / "pending.md").read_text(encoding="utf-8"))
        (base / "pending.answer").write_text(f"round:{rounds['r1']}\n1\n",
                                             encoding="utf-8")

    t1 = threading.Thread(target=writer1)
    t1.start()
    assert ui.ask("第一问", ["甲", "乙"]).indices == [0]
    t1.join(timeout=5)
    assert not t1.is_alive()

    result: dict = {}

    def asker2():
        result["choice"] = ui.ask("第二问", ["丙", "丁"])

    t2 = threading.Thread(target=asker2)
    t2.start()
    for _ in range(200):
        if (base / "pending.md").exists():
            break
        time.sleep(0.005)
    time.sleep(0.05)                        # 等 asker 进入轮询
    (base / "pending.answer").write_text(f"round:{rounds['r1']}\n2\n",
                                         encoding="utf-8")   # 上一轮的迟到答案
    t2.join(timeout=0.3)
    assert t2.is_alive(), "第 2 轮把上一轮的迟到答案收走了（B3 竞态复现）"
    assert "choice" not in result
    text = (base / "pending.md").read_text(encoding="utf-8")
    assert "轮次" in text and "无效" in text
    r2 = read_pending_round(text)
    (base / "pending.answer").write_text(f"round:{r2}\n2\n", encoding="utf-8")
    t2.join(timeout=5)
    assert not t2.is_alive()
    assert result["choice"].indices == [1]


def test_b3_roundless_answer_during_live_ask_not_consumed(tmp_path):
    """live 轮次里出现**无轮次**答案（repl 无 pending.md 回落路径「照写」的产物
    在任何窗口留到轮询开始）→ 拒收 + 提示；本轮盖章答案照收。"""
    base = tmp_path / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0, stdin=FakeStdin(False))
    result: dict = {}

    def asker():
        result["choice"] = ui.ask("问题？", ["甲", "乙"])

    t = threading.Thread(target=asker)
    t.start()
    for _ in range(200):
        if (base / "pending.md").exists():
            break
        time.sleep(0.005)
    time.sleep(0.05)
    (base / "pending.answer").write_text("1\n", encoding="utf-8")   # 无轮次
    t.join(timeout=0.3)
    assert t.is_alive(), "无轮次答案被 live 轮次收走了（B3）"
    text = (base / "pending.md").read_text(encoding="utf-8")
    assert "无效" in text and "轮次" in text
    (base / "pending.answer").write_text(_stamped(base, "2\n"), encoding="utf-8")
    t.join(timeout=5)
    assert not t.is_alive()
    assert result["choice"].indices == [1]


def test_write_answer_stamps_round(tmp_path):
    """write_answer 从 pending.md 机器头抄轮次盖进答案文件（协议 v2）。"""
    from harness.run.approval import write_answer

    base = tmp_path / ".harness"
    tok = _write_pending(base)
    write_answer(base, indices=[2], note="先看看")
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"round:{tok}"
    assert lines[1] == "2" and lines[2] == "备注:先看看"


def test_write_answer_legacy_pending_no_round(tmp_path):
    """旧格式 pending.md（无轮次头）→ 写无轮次答案（兼容；waiter 以「旧格式」拒收并提示）。"""
    from harness.run.approval import write_answer

    base = tmp_path / ".harness"
    base.mkdir(parents=True)
    (base / "pending.md").write_text("# 待审批\n\n问题？\n\n1. 改\n2. 不改\n",
                                     encoding="utf-8")
    write_answer(base, indices=[1])
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "1"


def test_cancel_answer_stamps_round_when_pending_present(tmp_path):
    """cancel_answer 照抄 pending.md 轮次（迟到的取消同样不得杀下一轮）；无 pending.md → 裸 c。"""
    from harness.run.approval import cancel_answer

    base = _pending_with_round(tmp_path)
    cancel_answer(base)
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"round:{ROUND_A}" and lines[1] == "c"
    bare = tmp_path / "bare"
    bare.mkdir()
    cancel_answer(bare)
    assert (bare / "pending.answer").read_text(encoding="utf-8") == "c\n"


def test_write_answer_valid(tmp_path):
    from harness.run.approval import write_answer

    base = tmp_path / ".harness"
    tok = _write_pending(base)
    echo = write_answer(base, indices=[2], note="先看看")
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"round:{tok}"          # 协议 v2：首行轮次（/answer 自动抄）
    assert lines[1] == "2"
    assert lines[2] == "备注:先看看"
    assert "已写入 pending.answer：2" in echo and "先看看" in echo


def test_write_answer_out_of_range_raises_and_does_not_write(tmp_path):
    """越界 → InvalidAnswerError 且**绝不写** pending.answer（Review Focus #2）。"""
    from harness.run.approval import InvalidAnswerError, write_answer

    base = tmp_path / ".harness"
    _write_pending(base, options=("改", "不改"))
    with pytest.raises(InvalidAnswerError):
        write_answer(base, indices=[5])
    assert not (base / "pending.answer").exists(), "非法答案不该写进文件（run 会一直等）"


def test_write_answer_single_multi_violation(tmp_path):
    """单选卡给多个选项号 → InvalidAnswerError 不写；多选卡给两个号 → 合法。"""
    from harness.run.approval import InvalidAnswerError, write_answer

    base = tmp_path / ".harness"
    _write_pending(base, options=("改", "不改"), multi=False)
    with pytest.raises(InvalidAnswerError):
        write_answer(base, indices=[1, 2])
    assert not (base / "pending.answer").exists()
    _write_pending(base, options=("改", "不改"), multi=True)
    write_answer(base, indices=[1, 2])
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("round:") and lines[1] == "1 2"


def test_write_answer_missing_pending_raises(tmp_path):
    """pending.md 不存在 → PendingMissingError（repl 在别处决定是否回落不校验直写）。"""
    from harness.run.approval import PendingMissingError, write_answer

    with pytest.raises(PendingMissingError):
        write_answer(tmp_path / ".harness", indices=[1])
    assert not (tmp_path / ".harness" / "pending.answer").exists()


def test_cancel_answer(tmp_path):
    """cancel_answer 写 "c\\n" —— wait_for_answer 的 _CANCEL_MARKERS 语义原样复用。"""
    from harness.run.approval import _CANCEL_MARKERS, cancel_answer

    base = tmp_path / ".harness"
    base.mkdir(parents=True)
    echo = cancel_answer(base)
    text = (base / "pending.answer").read_text(encoding="utf-8")
    assert text == "c\n"
    assert text.strip().casefold() in _CANCEL_MARKERS
    assert "c（取消）" in echo


def test_repl_answer_uses_shared_write(tmp_path, monkeypatch):
    """repl /answer 瘦身后走共享 write_answer（搬语义不复制校验逻辑）。"""
    from harness import repl
    from harness.repl import _cmd_answer

    class _Sess:
        pass

    store = ProjectStore(tmp_path)
    sess = _Sess()
    sess.store = store
    base = store.root / ".harness"
    _write_pending(base)

    calls = []
    orig = repl.write_answer

    def spy(base_, *, indices, note=None):
        calls.append({"base": base_, "indices": indices, "note": note})
        return orig(base_, indices=indices, note=note)

    monkeypatch.setattr(repl, "write_answer", spy)
    _cmd_answer(sess, "2 备注:同意")
    assert calls == [{"base": base, "indices": [2], "note": "同意"}]
    lines = (base / "pending.answer").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("round:") and lines[1] == "2"
