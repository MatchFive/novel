"""审批选项卡：终端交互 + 非 TTY 文件退化。

非 TTY 退化协议（design doc §2.1/§2.4，**v2，B3/2026-11-19**）：
ask 检测到非 TTY（或显式传入 ``non_tty_path``）时，把问题+选项写入
``<base>/pending.md``（机器头带 ``round=<hex>`` 轮次），然后无限轮询
``<base>/pending.answer``。pending.answer 形态（REPL ``/answer`` / web 应答 API
经 :func:`write_answer` 写入，轮次自动从 pending.md 抄）::

    round:<本轮轮次>      ← 第一行，/answer 自动填写
    1 2                  ← 第二行，选项号（空格分隔可多选）
    备注:…               ← 第三行可选

读到**本轮轮次**的合法答案后删除两个文件并返回 ``Choice``；轮次不符或无轮次
（旧格式/迟到的答案）**永不消费**，按 P15 口径在 pending.md 末尾附注原因——
上一轮审批刚结束就敲下的迟到 /answer 因此不会被下一轮读走（B3 竞态收口）。
第一行填 ``c``/``cancel``/``取消`` 视为取消，抛 ``ApprovalCancelled``
（取消不动数据，由调用方决定重问或挂起）；取消同样带轮次
（:func:`cancel_answer` 照抄，无 pending.md 时退化为裸 ``c``）。

本地审批等待不设超时（设计：审批超时 无限（本地））。轮询间隔由
``poll_interval`` 构造参数控制（默认 0.5 秒），测试传 0 即跳过 sleep。
"""

from __future__ import annotations

import asyncio
import re
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ApprovalCancelled",
    "ApprovalUI",
    "Choice",
    "InvalidAnswerError",
    "PendingMissingError",
    "ask_ui_async",
    "cancel_answer",
    "confirm_destructive",
    "parse_answer_line",
    "parse_pending_md",
    "read_pending_header",
    "read_pending_round",
    "render_card",
    "terminal_ask",
    "write_answer",
]

#: pending.answer 第一行的取消标记（casefold 后比较：CANCEL/Cancel 同样取消）。
_CANCEL_MARKERS = {"c", "cancel", "取消"}


class ApprovalCancelled(Exception):
    """用户取消审批。取消不动数据，由调用方决定重问或挂起。"""


class PendingMissingError(Exception):
    """pending.md 不存在（还没问 / 已被答）。应答 API 映射 409；repl 自行决定是否回落。"""


class InvalidAnswerError(Exception):
    """答案非法（越界 / 单选多给 / 空选）。**绝不写** pending.answer；应答 API 映射 400。"""


@dataclass
class Choice:
    """一次审批选择：选中的选项下标（0 起）+ 可选自由文本备注。"""

    indices: list[int]
    note: str | None = None


def render_card(question: str, options: list[str], multi: bool) -> str:
    """渲染 ┌ 边框选项卡。"""
    rows = [f"? {question}", *(f"  {i}) {o}" for i, o in enumerate(options, 1))]
    if multi:
        rows.append("  (多选：空格分隔选项号；行内可附 备注:...)")
    width = max(len(r) for r in rows)
    bar = "─" * (width + 2)
    return "\n".join([f"┌{bar}┐", *(f"│ {r.ljust(width)} │" for r in rows), f"└{bar}┘"])


def parse_answer_line(line: str, count: int, multi: bool) -> Choice | None:
    """解析一行答案：选项号（1 起，对应卡片/pending.md 中的编号）+ 可选 ``备注:...``。

    越界、非数字、单选多选冲突等一律返回 None（视为尚未给出合法答案）。
    """
    indices: list[int] = []
    note: str | None = None
    for tok in line.split():
        if tok.startswith("备注:"):
            note = tok[len("备注:") :] or None
            continue
        if not tok.isdigit():
            return None
        n = int(tok)
        if not 1 <= n <= count:
            return None
        idx = n - 1
        if idx not in indices:
            indices.append(idx)
    if not indices:
        return None
    if not multi and len(indices) > 1:
        return None
    return Choice(indices=indices, note=note)


def terminal_ask(
    question: str,
    options: list[str],
    multi: bool = False,
    note: str | None = None,
    *,
    inp: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> Choice:
    """终端读入：渲染选项卡后按行读答案。

    数字选择、空格分隔多选、行内 ``备注:...``；空输入回车两次或
    Ctrl+C / EOF 取消 → ``ApprovalCancelled``。
    """
    out(render_card(question, options, multi))
    empty_streak = 0
    while True:
        try:
            line = inp("选项(空格分隔)> " if multi else "选项> ").strip()
        except (KeyboardInterrupt, EOFError):
            raise ApprovalCancelled() from None
        if not line:
            empty_streak += 1
            if empty_streak >= 2:
                raise ApprovalCancelled()
            continue
        empty_streak = 0
        choice = parse_answer_line(line, len(options), multi)
        if choice is not None:
            if note is not None and choice.note is None:
                choice.note = note
            return choice
        out("无法解析，请输入选项号" + ("（空格分隔可多选）" if multi else ""))


#: pending.md 里的机器可读头（**独立复审 M1**：靠「行首数字 + . 」数选项会**多计**
#: —— 选项本身含换行、或问题正文里带编号列表时，计数会偏大 → ``/answer 4`` 被接受、
#: 而 run 侧只有 3 个选项 → 继续等，用户以为答了）。
#: **v2（B3）**：尾部可带 ``round=<hex>`` 轮次（ask 每次现造；/answer 抄进答案文件，
#: run 只收本轮的——迟到的上一轮回答案不被错收）。旧格式（无 round）兼容解析。
_PENDING_HEADER_RE = re.compile(
    r"^<!--\s*harness:options=(\d+)\s+multi=(\d+)(?:\s+round=([0-9a-f]+))?\s*-->\s*$",
    re.MULTILINE)


def _render_pending(question: str, options: list[str], multi: bool, *,
                    round: str | None = None) -> str:
    """渲染 pending.md（机器头 + 问题 + 选项）。

    ``round``（B3，协议 v2）：本轮轮次，写进机器头；None → 旧格式（无轮次段，
    兼容渲染/旧 pending.md）。生产 ask 必传（:meth:`ApprovalUI._ask_via_files`）。
    """
    hint = "（空格分隔可多选）" if multi else ""
    head = (f"<!-- harness:options={len(options)} multi={1 if multi else 0}"
            + (f" round={round}" if round else "") + " -->")
    lines = [
        "# 待审批",
        "",
        head,
        "",
        question,
        "",
        f"第一行填选项号{hint}，第二行可选备注；第一行填 c / 取消 表示取消。",
        "",
    ]
    lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
    lines.append("")
    return "\n".join(lines)


def read_pending_header(text: str) -> tuple[int, bool] | None:
    """从 pending.md 解析 ``(选项数, 是否多选)``；读不到（无头/旧格式）→ None。

    ``/answer`` 用它做写前校验（不再靠「数行首数字」——那会多计）。
    """
    m = _PENDING_HEADER_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), bool(int(m.group(2)))


def read_pending_round(text: str) -> str | None:
    """从 pending.md 机器头取本轮轮次（B3，协议 v2）；无头/旧格式 → None。

    ``write_answer`` / ``cancel_answer`` 抄进答案文件；waiter 据它只收本轮的答案。
    """
    m = _PENDING_HEADER_RE.search(text or "")
    if not m or not m.group(3):
        return None
    return m.group(3)


#: pending.md 选项行（``1. 改``）——parse_pending_md 收选项用。
_OPTION_LINE_RE = re.compile(r"^(\d+)\.\s+(.*)$")


def parse_pending_md(text: str) -> dict | None:
    """pending.md → ``{"question", "options": [...], "count", "multi"}``；无头/坏格式 → None。

    选项数以机器可读头为准（``read_pending_header``，不靠数行——选项含换行/正文带
    编号列表会多计）；选项文本收 ``数字. `` 行，超过头计数时取末尾 count 个
    （真选项是尾部连续块，正文里混的编号列表在前）。问题正文取头之后、提示行
    （``第一行填选项号…``）与选项行之前的非空行。
    """
    text = text or ""
    header = read_pending_header(text)
    if header is None:
        return None
    count, multi = header
    m = _PENDING_HEADER_RE.search(text)
    body = text[m.end():] if m else ""
    options: list[str] = []
    question_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        opt = _OPTION_LINE_RE.match(stripped)
        if opt:
            options.append(opt.group(2))
            continue
        if not stripped or stripped.startswith("第一行填选项号"):
            continue
        if not options:
            question_lines.append(stripped)
    if count and len(options) > count:
        options = options[len(options) - count:]
    return {"question": "\n".join(question_lines), "options": options,
            "count": count, "multi": multi}


def _pending_option_count(text: str) -> int:
    """pending.md 里列出的选项数（形如「1. 改」）——**旧格式**的回落计数。

    新格式用机器可读头（``read_pending_header``）→ 不会因选项含换行/正文带编号而多计
    （独立复审 M1）。此函数只服务于**没有头**的旧 pending.md。
    """
    count = 0
    for line in text.splitlines():
        head = line.strip()
        if head and head[0].isdigit() and ". " in head[:4]:
            count += 1
    return count


def write_answer(base: Path, *, indices: list[int], note: str | None = None) -> str:
    """共享的 /answer 校验 + 写盘（repl 与 web 同一逻辑）。

    - pending.md 不存在 → raise :class:`PendingMissingError`（**不写字**，Review Focus #2）；
    - 取消走 :func:`cancel_answer`（写 ``"c"`` 行，复用 ``_CANCEL_MARKERS`` 语义）；
    - 越界 / 单选多给 → raise :class:`InvalidAnswerError`（**不写字**）；
    - 合法 → 写 pending.answer（**协议 v2，B3**：首行 ``round:<本轮轮次>``——自动从
      pending.md 机器头抄；pending.md 是旧格式（无轮次）时写无轮次答案，waiter 会
      以「旧格式」拒收并在 pending.md 附注），其后选项号行、``备注:`` 行。

    校验先查机器可读头，无头回落 ``_pending_option_count``（旧格式兼容）。
    """
    base = Path(base)
    pending = base / "pending.md"
    if not pending.exists():
        raise PendingMissingError(f"没有待审批的问题：{pending} 不存在")
    try:
        text = pending.read_text(encoding="utf-8")
    except OSError:
        text = ""
    header = read_pending_header(text)
    if header is None:
        # 旧格式（无机器可读头）→ 回落「数行首数字」（可能多计，仅作兼容）
        legacy = _pending_option_count(text)
        header = (legacy, False) if legacy else None
    nums = [str(i) for i in indices]
    if header:
        count, multi = header
        if count:
            bad = [n for n in nums if not 1 <= int(n) <= count]
            if bad:
                raise InvalidAnswerError(f"/answer 拒绝写入：选项号 {('、'.join(bad))} 越界"
                                         f"（本次共 {count} 个选项）——见 {pending}")
            if not multi and len(nums) > 1:
                raise InvalidAnswerError(f"/answer 拒绝写入：本次是**单选**（共 {count} 个选项），"
                                         f"却给了 {len(nums)} 个选项号——见 {pending}")
    round_token = read_pending_round(text)
    lines = ([f"round:{round_token}"] if round_token else []) + [" ".join(nums)]
    if note:
        lines.append(f"备注:{note}")
    (base / "pending.answer").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"已写入 pending.answer：{' '.join(nums)}" + (f"（备注：{note}）" if note else "")


def cancel_answer(base: Path) -> str:
    """写 ``"c"`` 取消当前审批（``wait_for_answer`` 读到抛 :class:`ApprovalCancelled`）。

    **协议 v2（B3）**：pending.md 机器头有轮次时照抄进答案文件首行——迟到的取消
    同样不得杀掉**下一轮**的审批；无 pending.md / 旧格式 → 裸 ``c``（现状语义）。
    """
    base = Path(base)
    pending = base / "pending.md"
    round_token = None
    if pending.exists():
        try:
            round_token = read_pending_round(pending.read_text(encoding="utf-8"))
        except OSError:
            round_token = None
    lines = ([f"round:{round_token}"] if round_token else []) + ["c"]
    (base / "pending.answer").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "已写入 pending.answer：c（取消）"


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class ApprovalUI:
    """审批入口：TTY 走终端选项卡，非 TTY 退化为 pending.md/pending.answer 文件协议。

    测试缝：
    - ``ask`` 注入假实现即可完全绕过终端/文件 IO；
    - ``stdin`` 注入带 ``isatty()`` 的桩控制 TTY 判定；
    - ``non_tty_path`` 非 None 时强制文件模式（并把 pending 文件落到该目录）；
    - ``file_base`` 指定**自动文件模式**的落盘目录（不强制进文件模式）——REPL 传项目根，
      与 ``/answer`` 写 pending.answer 的位置一致；
    - ``poll_interval=0`` 时轮询不 sleep（本地等待本身无超时上限）。
    """

    def __init__(
        self,
        ask: Callable[[str, list[str], bool], Choice] | None = None,
        out: Callable[[str], None] = print,
        *,
        non_tty_path: Path | None = None,
        poll_interval: float = 0.5,
        stdin=None,
        file_base: Path | None = None,
    ):
        self._custom_ask = ask  # 显式注入的 ask 拥有最高优先权：完全绕过终端/文件 IO（测试缝）
        self._ask = ask or terminal_ask
        self._out = out
        self._non_tty_path = Path(non_tty_path) if non_tty_path is not None else None
        # 文件模式默认目录：构造时锚定绝对路径（cwd 解析一次固定）——相对 .harness 在
        # 运行中 chdir 后会与外部写入方（如 REPL /answer 的绝对写）分叉，永远等不到答案。
        self._default_base = Path(".harness").resolve()
        # **文件模式落盘目录**（P14）：默认是 **cwd** 下的 .harness —— 但 REPL 的
        # ``/answer`` 写的是**项目根**下的 .harness → cwd ≠ 项目根时两边分叉，
        # run 永远等不到答案（挂死）。→ 调用方显式传 file_base=项目根。
        self._file_base = Path(file_base) if file_base is not None else None
        self._poll_interval = poll_interval
        self._stdin = stdin if stdin is not None else sys.stdin

    def ask(self, question: str, options: list[str], multi: bool = False, note: str | None = None) -> Choice:
        if self._custom_ask is not None:
            return self._custom_ask(question, options, multi, note)
        if self._non_tty_path is not None or not self._stdin.isatty():
            return self._ask_via_files(question, options, multi=multi, note=note)
        return self._ask(question, options, multi, note)

    @property
    def file_mode(self) -> bool:
        """当前 ask 是否走 pending.md/pending.answer 文件协议（终端路径 / 注入 ask 为 False）。

        web 世界靠它选异步等待：文件协议的轮询等待有 async 版（``ask_async``），
        同步 ``time.sleep`` 轮询会把 uvicorn 的事件循环整个冻住（2026-11-18 验收 Critical ①：
        run 挂起审批 → 答题 API 与 SSE 全冻）。
        """
        return (self._custom_ask is None
                and (self._non_tty_path is not None or not self._stdin.isatty()))

    async def ask_async(self, question: str, options: list[str], multi: bool = False,
                        note: str | None = None) -> Choice:
        """``ask`` 的 async 版：文件协议等待用 ``asyncio.sleep`` 轮询，不阻塞事件循环。

        语义与 ``ask`` 逐字一致（同一 pending 文件、同一解析、同一 finally 清理）；
        仅等待原语不同。终端路径（仅 repl TTY 会走到）委托线程执行，行为不变。
        """
        if self._custom_ask is not None:
            return self._custom_ask(question, options, multi, note)
        if self.file_mode:
            return await self._ask_via_files_async(question, options, multi=multi, note=note)
        return await asyncio.to_thread(self._ask, question, options, multi, note)

    def _pending_base(self) -> Path:
        return (self._non_tty_path if self._non_tty_path is not None
                else (self._file_base if self._file_base is not None
                      else self._default_base))

    def _ask_via_files(self, question: str, options: list[str], *, multi: bool, note: str | None) -> Choice:
        base = self._pending_base()
        base.mkdir(parents=True, exist_ok=True)
        pending = base / "pending.md"
        answer = base / "pending.answer"
        # 终审 Finding ③：写新问题的 pending.md 前先清残留 pending.answer——上一轮
        # 「答案已写盘、waiter 尚未消费」时被杀/重启留下的合法格式答案，会被本轮 ask
        # 秒消费成对新问题的错答（静默错答）。
        _silent_unlink(answer)
        # B3：本轮轮次——pending.md 机器头与 waiter 的期望同一只 token；迟到的
        # 上一轮答案（轮次不符/无轮次）因此永不被本轮读走。
        round_token = secrets.token_hex(8)
        pending.write_text(_render_pending(question, options, multi, round=round_token),
                           encoding="utf-8")
        try:
            choice = self.wait_for_answer(answer, count=len(options), multi=multi,
                                          round=round_token)
        finally:
            _silent_unlink(pending)
            _silent_unlink(answer)
        if note is not None and choice.note is None:  # Task 12 挂点：ask 的 note 作文件模式 fallback
            choice.note = note
        return choice

    async def _ask_via_files_async(self, question: str, options: list[str], *, multi: bool,
                                   note: str | None) -> Choice:
        """文件协议的 async 版：与 ``_ask_via_files`` 同一套盘上面，只换等待原语。"""
        base = self._pending_base()
        base.mkdir(parents=True, exist_ok=True)
        pending = base / "pending.md"
        answer = base / "pending.answer"
        # 与 ``_ask_via_files`` 同口径（终审 Finding ③）：新 ask 不得消费上一轮残留的答案。
        _silent_unlink(answer)
        round_token = secrets.token_hex(8)          # B3：同口径，见同步版注释
        pending.write_text(_render_pending(question, options, multi, round=round_token),
                           encoding="utf-8")
        try:
            choice = await self.wait_for_answer_async(answer, count=len(options), multi=multi,
                                                      round=round_token)
        finally:
            _silent_unlink(pending)
            _silent_unlink(answer)
        if note is not None and choice.note is None:
            choice.note = note
        return choice

    def _try_read_answer(self, answer: Path, *, count: int, multi: bool,
                         round: str | None = None) -> Choice | None:
        """读一次 pending.answer：合法 → Choice；取消标记 → ApprovalCancelled；无文件/非法
        （含**轮次不符**，B3）→ None（按 P15 在 pending.md 末尾附注原因）。

        协议 v2：首行 ``round:<轮次>`` 由 /answer 自动抄；``round``（waiter 的本轮轮次）
        非 None 时，答案轮次不符或**无轮次（旧格式/迟到回落路径）一律拒收**——
        上一轮审批刚结束就敲下的 /answer 不会被下一轮读走。``round=None``（缺省）=
        不校验，仅测试/旧直接调用方。
        """
        if not answer.exists():
            return None
        try:
            text = answer.read_text(encoding="utf-8")
        except OSError:
            text = ""
        lines = text.splitlines()
        stamped: str | None = None
        if lines and lines[0].strip().startswith("round:"):
            stamped = lines[0].strip()[len("round:"):].strip()
            lines = lines[1:]
        if round is not None and stamped != round:
            # B3：别轮/旧格式的答案 → 永不消费；告诉答的人为什么（P15 同口径）
            self._note_wrong_round(answer, stamped)
            return None
        first = lines[0].strip() if lines else ""
        if first.casefold() in _CANCEL_MARKERS:
            raise ApprovalCancelled()
        choice = parse_answer_line(first, count, multi)
        if choice is None:
            # 非法/越界/半写入：视为还没答好 → **告诉答的人**（P15）
            self._note_invalid_answer(answer, first, count, multi)
            return None
        if len(lines) > 1 and lines[1].strip():
            choice.note = lines[1].strip().removeprefix("备注:") or None
        return choice

    def wait_for_answer(self, answer: Path, *, count: int, multi: bool,
                        round: str | None = None) -> Choice:
        """无限轮询 pending.answer，读到合法答案即返回；取消标记抛 ApprovalCancelled。

        间隔 ``poll_interval`` 秒；为 0 时不 sleep（测试用）。等待本身不设超时。

        ``round``（B3，协议 v2）：本轮轮次；非 None 时只收``round:`` 行与之相等的
        答案（无轮次/别轮一律拒收并附注）。生产路径（``_ask_via_files*``）必传；
        None = 不校验（旧直接调用方/测试）。

        **P15：非法答案要有反馈**——此前「越界/半写入」只是**继续等**，答的人**看不到任何
        提示**（run 静默挂住，人以为答了却没反应）。→ 在 ``pending.md`` 末尾**追加**一行
        说明（谁在等、上一条答案为什么无效、该怎么写），答的人下一次看 pending.md 就能看见。
        """
        while True:
            choice = self._try_read_answer(answer, count=count, multi=multi, round=round)
            if choice is not None:
                return choice
            if self._poll_interval > 0:
                time.sleep(self._poll_interval)

    async def wait_for_answer_async(self, answer: Path, *, count: int, multi: bool,
                                    round: str | None = None) -> Choice:
        """``wait_for_answer`` 的 async 版：``asyncio.sleep`` 轮询，等待期间事件循环可跑
        别的任务（web 审批自锁的修法，2026-11-18 验收 Critical ①）。语义逐字一致
        （含 B3 轮次校验，同参数同口径）。"""
        while True:
            choice = self._try_read_answer(answer, count=count, multi=multi, round=round)
            if choice is not None:
                return choice
            if self._poll_interval > 0:
                await asyncio.sleep(self._poll_interval)

    def _append_pending_note(self, pending: Path, note: str, dedup_key: str) -> None:
        """在 pending.md 末尾追加提示（答的人能看见为什么没生效）；同键去重。"""
        if not pending.exists():
            return
        try:
            text = pending.read_text(encoding="utf-8")
            # 去重键按**提示原因**给（独立复审 M2 口径：另一条不同的原因照样追加）
            if f"<!-- {dedup_key} -->" in text:
                return
            pending.write_text(text.rstrip() + "\n" + note
                               + f"<!-- {dedup_key} -->\n", encoding="utf-8")
        except OSError:
            pass

    def _note_wrong_round(self, answer: Path, stamped: str | None) -> None:
        """B3：答案轮次不符/无轮次 → 在 pending.md 说明（旧格式答案需经 /answer 重答）。"""
        pending = answer.with_name("pending.md")
        reason = ("没有轮次标识（旧格式或还没问到就写了）——请用 /answer 或应答按钮"
                  "按**当前**问题重答（轮次由它自动填写）" if stamped is None
                  else "属于**另一轮**审批（轮次不符）——这份答案已过期，请按当前问题重答")
        note = (f"\n> ⚠ 上一条答案**无效**（{reason}）"
                f" —— 可改写 {answer.name} 后重试，或用 /answer 命令作答。\n")
        self._append_pending_note(pending, note, dedup_key=f"last-invalid=round:{stamped!r}")

    def _note_invalid_answer(self, answer: Path, first: str, count: int,
                             multi: bool) -> None:
        """在 pending.md 末尾追加「上一条答案无效」提示（答的人能看见为什么没生效）。"""
        pending = answer.with_name("pending.md")
        reason = (f"选项号越界（本次共 {count} 个选项）" if first.isdigit()
                  else "第一行必须是选项号（数字）")
        if multi and first.count(" ") > 1 and not first.split()[0].isdigit():
            reason = "多选时请用空格分隔选项号"
        note = (f"\n> ⚠ 上一条答案**无效**（{reason}）：{first!r}"
                f" —— 本次共 {count} 个选项，请改写 {answer.name} 的第一行后重试。\n")
        # 去重键 = **上一次非法答案的内容**（独立复审 M2）：原先用「文件里出现过提示」
        # 做**全局**去重 → **另一条**不同的非法答案不再追加提示 → 用户看不到新错误的原因。
        self._append_pending_note(pending, note, dedup_key=f"last-invalid={first!r}")


async def ask_ui_async(ui: ApprovalUI, question: str, options: list[str],
                       multi: bool = False) -> Choice:
    """async 上下文（web 路由 / run 驱动 / fix 任务）里的审批询问统一入口。

    文件协议（``ui.file_mode``）走 ``ask_async``——``asyncio.sleep`` 轮询不占事件循环
    （2026-11-18 验收 Critical ①）；终端路径（repl TTY）与测试假 UI（无 ``file_mode``）
    保持同步 ``ask`` 调用，行为逐字不变。
    """
    if getattr(ui, "file_mode", False):
        return await ui.ask_async(question, options, multi)
    return ui.ask(question, options, multi)


def confirm_destructive(ui: ApprovalUI, what: str) -> bool:
    """危险操作确认：选项必须含字面「确认清空」项，选中它才返回 True。"""
    options = ["确认清空", "取消"]
    choice = ui.ask(f"将清空{what}，该操作不可撤销。", options)
    return choice.indices == [options.index("确认清空")]
