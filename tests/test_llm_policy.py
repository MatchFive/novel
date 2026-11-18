from harness.llm.policy import StarvedAtCapError, resolve_max_tokens


def test_starved_at_cap_error_is_exception_with_message():
    exc = StarvedAtCapError("cap 32768 仍被截断")
    assert isinstance(exc, Exception)
    try:
        raise exc
    except StarvedAtCapError as caught:
        assert str(caught) == "cap 32768 仍被截断"


def test_baseline():
    assert resolve_max_tokens(1000) == 8192

def test_thinking_triples():
    assert resolve_max_tokens(1000, thinking=True) == 24576

def test_input_scales():
    assert resolve_max_tokens(20000) == 32768  # 输入×2 超 cap → 封顶

def test_thinking_capped():
    assert resolve_max_tokens(20000, thinking=True) == 32768
