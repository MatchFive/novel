"""P12–P15 合并复审（2026-11-17）的修复回归测试。

逐条对应复审报告的发现：
- **R1**：`/issues` 与 `/skill import` 的审批 UI 也要落**项目根**（否则 pending 落 cwd、
  `/answer` 写项目根 → 两边分叉 → **挂死**）。
- **R2**：`/answer` 支持**取消标记** `c`（协议支持，`/answer` 此前产生不了 → 取消不可达）。
- **R3**：`parse_write_range` 的**方向/否定**词（「从第二卷开始写」「不写第一卷」）→
  **不限制**（曾被判成闭区间 → 误拦；「不写第一卷」曾被判成「只许写第 1 卷」，**正好反了**）。
- **M1**：选项数靠 **pending.md 的机器可读头**（不再数行首数字 → 多行选项/编号正文会多计）。
- **M2**：非法答案提示的**去重键 = 答案内容**（原先全局去重 → 另一条非法答案不再提示）。
- **M3**：范围闸的观测判 **noop**（0 产出不得计有效步/清 noop；且**不得**判 failed
  ——那会进按工具分桶的冷却 → 连合法的卷也调不了）。
- **M4**：段三的每卷章数与段二**同口径**（都用预计总卷数）。
- **补充**：`/answer` 在**单选** pending 上给了多个选项号 → 拒绝写入。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.projects import create_project
from harness.repl import _cmd_answer
from harness.run.approval import (
    ApprovalCancelled,
    ApprovalUI,
    _render_pending,
    read_pending_header,
)
from harness.run.scope import parse_write_range
from harness.session import RunSession as _Session
from harness.store.project import ProjectStore


def _settings():
    from harness.config import ProviderConfig, Settings

    return Settings(library_root=Path("x"),
                    providers={"test": ProviderConfig(base_url="https://api.test",
                                                      api_key="k", lite="m",
                                                      standard="m", pro="m")},
                    default_provider="test", default_tier="standard",
                    web_verify=True)


def _session(tmp_path, name="书"):
    info = create_project(tmp_path, name)
    store = ProjectStore(info.path)
    sess = _Session(info, store, _settings())
    return sess, store


class _Stdin:
    def isatty(self) -> bool:
        return False


# ------------------------------------------------------------------ R3

def test_r3_direction_words_are_not_limited():
    """**方向/开区间**词 → **不限制**（闭区间表达不了「第 N 卷及以后」）。"""
    for text in ("从第二卷开始写", "第二卷以后", "第三卷往后", "从第 2 卷起"):
        assert parse_write_range(text) is None, text


def test_r3_negation_words_are_not_limited():
    """**否定/排除**词 → **不限制**（「不写第一卷」曾被判成「只许写第 1 卷」，**正好反了**）。"""
    for text in ("不写第一卷", "除了第一卷都写", "跳过第二卷"):
        assert parse_write_range(text) is None, text


def test_r3_plain_range_still_works():
    """正向闭区间**不受影响**。"""
    assert parse_write_range("只写第 3 卷") == (3, 3)
    assert parse_write_range("第二卷到第五卷") == (2, 5)


# ------------------------------------------------------------------ R1

def test_r1_all_repl_approval_uis_land_in_project_root():
    """**所有**审批 UI 都落**项目根**（不再有裸 `ApprovalUI()` 构造）。

    漏一处 = 非 TTY 下 pending 落 cwd、而 `/answer` 写项目根 → 两边分叉 → **挂死**。
    （approval_ui 工厂 W0 起搬到 harness.session；repl 全经 ``sess.approval_ui()`` 取用。）
    """
    from harness import repl as repl_mod
    from harness import session as session_mod

    # **AST** 扫（不用正则：文档串/注释里也会出现 "ApprovalUI(" 字样）
    tree = ast.parse(inspect.getsource(session_mod))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ApprovalUI"]
    assert calls, "approval_ui 工厂不得消失"
    # 每处构造都必须显式锚定目录（file_base 或 non_tty_path）——漏一处 = 非 TTY 下
    # pending 落 cwd、而 `/answer` 写项目根 → 两边分叉 → **挂死**。
    # （Task 21 Critical ②：web 会话经 non_tty_path 强制文件协议、与 isatty 解耦，
    #   构造点从 1 变 2——落点纪律不变，仍全部锚 store.root/.harness。）
    for call in calls:
        assert any(kw.arg in ("file_base", "non_tty_path") for kw in call.keywords), \
            "ApprovalUI 构造必须显式锚定目录（file_base/non_tty_path），不得裸构造"
    src = inspect.getsource(session_mod)
    assert "def approval_ui(self)" in src
    assert "self.store.root" in src
    # repl 侧零裸构造（全部委托 RunSession.approval_ui）
    repl_calls = [n for n in ast.walk(ast.parse(inspect.getsource(repl_mod)))
                  if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ApprovalUI"]
    assert repl_calls == [], "repl 里不得再裸建 ApprovalUI（经 sess.approval_ui 取用）"


def test_r1_session_approval_ui_uses_project_root(tmp_path):
    """``_Session.approval_ui()`` 的 file_base = 项目根的 .harness。"""
    _sess, store = _session(tmp_path, "落点书")
    ui = _sess.approval_ui()
    assert ui._file_base == store.root / ".harness"


# ------------------------------------------------------------------ R2

def test_r2_answer_supports_cancel_marker(tmp_path, capsys):
    """``/answer c`` → 写 `c` 进 pending.answer（协议据此抛 ApprovalCancelled → 取消不动数据）。"""
    sess, store = _session(tmp_path, "取消书")

    _cmd_answer(sess, "c")

    text = (store.root / ".harness" / "pending.answer").read_text(encoding="utf-8")
    assert text.strip().splitlines()[0].casefold() == "c"
    assert "取消" in capsys.readouterr().out


def test_r2_cancel_marker_reaches_protocol(tmp_path):
    """协议侧认这个标记（``wait_for_answer`` 抛 ApprovalCancelled）。"""
    base = tmp_path / ".harness"
    base.mkdir(parents=True)
    (base / "pending.answer").write_text("c\n", encoding="utf-8")
    ui = ApprovalUI(stdin=_Stdin(), file_base=base, poll_interval=0)
    with pytest.raises(ApprovalCancelled):
        ui.wait_for_answer(base / "pending.answer", count=2, multi=False)


# ------------------------------------------------------------------ M1

def test_m1_pending_header_reports_option_count_and_multi():
    """pending.md 带**机器可读头**（选项数 + 单/多选）→ /answer 不必数行。"""
    text = _render_pending("问题？", ["改\n3. 续行里的编号", "不改"], multi=True)
    assert read_pending_header(text) == (2, True)

    text2 = _render_pending("问题？", ["改", "不改"], multi=False)
    assert read_pending_header(text2) == (2, False)


def test_m1_answer_rejects_option_beyond_header(tmp_path, capsys):
    """选项含换行/正文带编号时，**选项数仍以头为准**（不多计）。"""
    sess, store = _session(tmp_path, "多行书")
    base = store.root / ".harness"
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending.md").write_text(
        _render_pending("问题？", ["改\n3. 这是选项内的续行", "不改"], multi=False),
        encoding="utf-8")

    _cmd_answer(sess, "3")

    assert "越界" in capsys.readouterr().out
    assert not (base / "pending.answer").exists()


def test_m1_answer_rejects_multi_on_single_choice(tmp_path, capsys):
    """**单选** pending 上给多个选项号 → 拒绝写入（run 侧 `parse_answer_line(multi=False)` 会拒）。"""
    sess, store = _session(tmp_path, "单选书")
    base = store.root / ".harness"
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending.md").write_text(
        _render_pending("问题？", ["改", "不改"], multi=False), encoding="utf-8")

    _cmd_answer(sess, "1 2")

    assert "单选" in capsys.readouterr().out
    assert not (base / "pending.answer").exists()


# ------------------------------------------------------------------ M2

def test_m2_different_invalid_answers_each_get_hint(tmp_path):
    """**不同的**非法答案 → **各自**追加提示（去重键 = 答案内容，不是「文件里有过提示」）。"""
    base = tmp_path / ".harness"
    base.mkdir(parents=True)
    pending = base / "pending.md"
    pending.write_text("# 待审批\n\n问题？\n\n1. 改\n2. 不改\n", encoding="utf-8")
    answer = base / "pending.answer"
    answer.write_text("9\n", encoding="utf-8")

    ui = ApprovalUI(stdin=_Stdin(), file_base=base, poll_interval=0)
    ui._note_invalid_answer(answer, "9", count=2, multi=False)
    text = pending.read_text(encoding="utf-8")
    assert "上一条答案**无效**" in text
    assert "9" in text

    # 第二条**不同**的非法答案 → 也要有提示（原先被全局标记压掉）
    answer.write_text("abc\n", encoding="utf-8")
    ui._note_invalid_answer(answer, "abc", count=2, multi=False)
    text2 = pending.read_text(encoding="utf-8")
    assert text2.count("上一条答案**无效**") == 2
    assert "abc" in text2

    # **同一条**再提示 → 不重复追加
    ui._note_invalid_answer(answer, "abc", count=2, multi=False)
    assert pending.read_text(encoding="utf-8").count("上一条答案**无效**") == 2


# ------------------------------------------------------------------ M3

def test_m3_range_gate_observation_is_noop():
    """范围闸的观测判 **noop**：不涨有效步、**要**涨 noop_streak，且**不进**失败账。

    判 failed 会进 P13 的按工具分桶冷却 → 连**合法的卷**也调不了（独立复审 X1 澄清）。
    """
    gate = ("NOPROGRESS: write_plot 未执行：第 2 卷不在本次写作范围内"
            "（本次范围：第 1–1 卷；依据需求解析的「只写第一卷」）")
    assert AgentLoop._classify(gate) == "noop"


def test_m3_range_gate_observation_is_not_failed():
    """**不得**判 failed（那是冷却死路的入口）。"""
    gate = "NOPROGRESS: write_plot 未执行：第 2 卷不在本次写作范围内"
    assert AgentLoop._classify(gate) != "failed"
    assert not gate.startswith("FAILED")


# ------------------------------------------------------------------ L1

def test_l1_user_facing_texts_do_not_mention_tty():
    """**用户可见的失败文案**里不该再提「非交互式终端（TTY）」。

    P14 之后 TTY **不再是**阻止这些命令的原因（只差 provider 没配）→ 文案提 TTY 会让
    用户误以为「换个终端就好了」。→ 只扫**真正打印出去的字符串**（AST：print 的实参）。
    """
    from harness import repl as repl_mod

    tree = ast.parse(inspect.getsource(repl_mod))
    printed: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    printed.append(arg.value)
    assert printed, "没扫到 print 文案（测试本身失效？）"
    bad = [t for t in printed if "TTY" in t or "非交互式终端" in t]
    assert not bad, f"用户可见文案仍在提 TTY：{bad}"


# ------------------------------------------------------------------ M4

def test_m4_stage3_uses_same_volume_basis_as_stage2(tmp_path):
    """段三与段二的**每卷章数口径一致**（都用预计总卷数）。

    段二说「每卷约 50 章」而段三说「约 333 章」→ 拆章模型被喂了错的容量参考。
    """
    from harness.planning.context import plot_units_vars

    _sess, store = _session(tmp_path, "口径书")
    (store.root / "README.md").write_text(
        "题材：仙侠\n基调：冷峻\n结局：开放\n每章字数：3000\n目标总章数：600\n"
        "预计卷数：20\n", encoding="utf-8")
    store.upsert_arcs([{"vol": 1, "title": "卷甲"}, {"vol": 2, "title": "卷乙"}],
                      planned_volume_count=20)

    vars_ = plot_units_vars(store, arc={"vol": 1, "seq": 1},
                            plot={"seq": 1, "title": "题", "summary": "概要"},
                            plots=[{"seq": 1, "title": "题", "summary": "概要"}],
                            target_words=3000)
    # 600 / 20 = 30（若用实际弧数 2 → 300）
    assert vars_["arc_chapters"] == "30"
