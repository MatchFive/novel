"""步决策解析 + 观察截断（任务 7，契约见计划附录 C.1）。"""

import pytest

from harness.agent import StepDecision, StepParseError, parse_step, trim_observation  # noqa: F401
from harness.run.constants import OBS_MAX


def test_tool_calls_take_priority():
    r = {"tool_calls": [{"name": "plan_arcs", "arguments": "{}"}], "content": '{"action":"finish"}'}
    d = parse_step(r)
    assert d.action == "plan_arcs"


def test_json_text_fallback_extracts_first_object():
    d = parse_step({"content": "我想想。{\"thought\": \"t\", \"say\": \"你好\", \"action\": \"ask_user\", \"args\": {\"q\": 1}} 后面废话"})
    assert d.action == "ask_user" and d.say == "你好"


def test_both_protocols_fail_raises():
    with pytest.raises(StepParseError):
        parse_step({"content": "完全不是 JSON"})


def test_trim_one_pass_no_while_loop():
    long_line = "字" * 5000
    out = trim_observation("\n".join([long_line, "短行", long_line]))  # noqa: FLY002
    assert len(out) <= OBS_MAX
    # 压过 80 字批后含短行原文
    assert "短行" in out


def test_json_scan_linear_many_unmatched_braces():
    # 回归：大量未匹配 '{' 不得触发 O(n²) 重扫冻死循环（截断响应事故同类）
    with pytest.raises(StepParseError):
        parse_step({"content": "{" * 2000 + "x"})


def test_trim_hard_batch_presses_longest_lines_to_80():
    # 8 行 × 240 字批后 = 1927 > 1000，触发硬批档：最长行压到 80，一遍即止
    out = trim_observation("\n".join(["字" * 300] * 8), max_len=1000)
    kept = out.split("\n")
    assert len(out) <= 1000
    assert max(map(len, kept)) <= 240
    assert min(map(len, kept)) == 80  # 最长行已被压到硬批限


def test_malformed_tool_calls_falls_back_to_content():
    r = {
        "tool_calls": [{"name": "broken", "arguments": "{not json"}],
        "content": '{"thought": "t", "action": "finish"}',
    }
    d = parse_step(r)
    assert d.action == "finish"


def test_malformed_tool_calls_and_bad_content_raises():
    r = {"tool_calls": [{"name": "broken", "arguments": "{oops"}], "content": "完全不是 JSON"}
    with pytest.raises(StepParseError):
        parse_step(r)
