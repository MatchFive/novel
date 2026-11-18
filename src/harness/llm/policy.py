"""max_tokens 策略（纯函数）。

字符→token 近似 1:1，与线上实现一致。
"""

BASELINE_MAX_TOKENS = 8192
THINKING_FACTOR = 3.0
INPUT_FACTOR = 2.0
CAP_MAX_TOKENS = 32768


class StarvedAtCapError(Exception):
    """重试已打到 cap 仍拿不到内容时抛出。"""


def resolve_max_tokens(input_chars: int, *, thinking: bool = False) -> int:
    tok = float(BASELINE_MAX_TOKENS)
    if thinking:
        tok *= THINKING_FACTOR
    tok = min(CAP_MAX_TOKENS, max(tok, input_chars * INPUT_FACTOR))
    return int(tok)
