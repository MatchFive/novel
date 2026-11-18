"""A8：write_plot/edit_by_command 签名表描述修复（模型可见文案写明用途与边界）。"""


def test_write_plot_signature_says_prose():
    from harness.agent.loop import _FALLBACK_SIG
    assert "正文" in _FALLBACK_SIG["write_plot"]
    assert "status=done" in _FALLBACK_SIG["write_plot"] or "写完" in _FALLBACK_SIG["write_plot"]


def test_edit_by_command_signature_states_scope():
    from harness.agent.loop import _FALLBACK_SIG
    sig = _FALLBACK_SIG["edit_by_command"]
    assert "不含章节标题" in sig and "正文" in sig
