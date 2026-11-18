"""步决策双协议解析 + 观察一遍截断（设计附录 C.1 契约）。"""

import json
from dataclasses import dataclass, field

from harness.run.constants import OBS_HARD_BATCH, OBS_MAX, OBS_SOFT_LINE
from harness.run.constants import SCAN_CAP as _SCAN_CAP

_ELLIPSIS = "…[截断]…"


@dataclass
class StepDecision:
    """单步决策：think/say 供日志与对白，action/args 驱动执行。"""

    thought: str
    say: str | None
    action: str
    args: dict = field(default_factory=dict)


class StepParseError(ValueError):
    """tool_calls 与 content JSON 双协议都解析不出步决策。"""


def parse_step(response) -> StepDecision:
    """解析 LLM 响应为 StepDecision。

    response 形态：``{"tool_calls": [...]} | {"content": str} | str``。
    tool_calls 非空 → 取第一个 call（name→action，arguments JSON→args）；
    否则从 content 提取第一个含 thought/action 键的可解析 JSON 对象；
    双协议都失败 → raise StepParseError。
    """
    tool_calls: list = []
    content = ""
    if isinstance(response, str):
        content = response
    elif isinstance(response, dict):
        raw_calls = response.get("tool_calls")
        if isinstance(raw_calls, list):
            tool_calls = raw_calls
        raw_content = response.get("content")
        if isinstance(raw_content, str):
            content = raw_content
    else:
        raise StepParseError(f"无法解析的响应类型: {type(response).__name__}")

    if tool_calls:
        try:
            call = tool_calls[0]
            action = call["name"]
            raw_args = call.get("arguments") or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise StepParseError("tool_call arguments 不是 JSON 对象")
            return StepDecision(thought="", say=None, action=action, args=args)
        except (KeyError, TypeError, ValueError):
            pass  # 落入 content 协议

    decision = _parse_content(content)
    if decision is None:
        raise StepParseError("tool_calls 与 content 双协议均解析失败")
    return decision


def _parse_content(content: str) -> StepDecision | None:
    """从 content 提取第一个含 thought/action 键的可解析 JSON 对象。"""
    for obj in _iter_json_objects(content):
        if "thought" in obj and "action" in obj:
            say = obj.get("say")
            args = obj.get("args")
            return StepDecision(
                thought=str(obj["thought"]),
                say=str(say) if say is not None else None,
                action=str(obj["action"]),
                args=dict(args) if isinstance(args, dict) else {},
            )
    return None


def _iter_json_objects(text: str):
    """单遍扫描记录括号配对（字符串/转义感知），只对配对 span 尝试 json.loads。

    线性：配对的 '}' 弹栈一次成形，不会因 K 个未匹配 '{' 退化成 K·n 次扫描
    （截断响应里 100KB 的裸 '{' 足以冻死循环，旧实现即此类事故）。
    """
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pairs.append((stack.pop(), i))
    for start, end in sorted(pairs):
        try:
            obj = json.loads(text[start : end + 1])
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _squash(line: str, limit: int) -> str:
    """把单行压到 limit 字：头尾各留一半，中间插省略标记。"""
    keep = limit - len(_ELLIPSIS)
    head = keep // 2
    return line[:head] + _ELLIPSIS + line[-(keep - head) :]


def trim_observation(text: str, max_len: int = OBS_MAX) -> str:
    """观察截断（C.1）：先按 SCAN_CAP 切片（超出只留头尾）；超长行压到 240 字；
    仍超 max_len 则最长行压到 80 字。一遍扫描 + 一次排序，禁止 while 反复压同一行。"""
    if len(text) > _SCAN_CAP:
        half = _SCAN_CAP // 2
        text = text[: half - len(_ELLIPSIS)] + _ELLIPSIS + text[-half:]

    # 一遍扫描：超长行压到软限
    lines = [_squash(line, OBS_SOFT_LINE) if len(line) > OBS_SOFT_LINE else line for line in text.split("\n")]
    out = "\n".join(lines)
    if len(out) <= max_len:
        return out

    # 一次排序：从最长行起压到硬批限，压一遍即止
    order = sorted(range(len(lines)), key=lambda i: len(lines[i]), reverse=True)
    for i in order:
        if len(out) <= max_len:
            break
        line = lines[i]
        if len(line) <= OBS_HARD_BATCH:
            continue
        lines[i] = _squash(line, OBS_HARD_BATCH)
        out = "\n".join(lines)
    # 契约允许此处 out 仍超 max_len：一遍扫描 + 一次排序，禁止 while 回头重压——勿"修复"。
    return out
