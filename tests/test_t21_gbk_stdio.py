"""Task 21 验收 Critical ③ 回归网：GBK 控制台上 💭 print 不再杀 run。

缺陷：``_record_step`` 的 💭 print 在 Windows GBK（cp936）控制台上抛
UnicodeEncodeError，杀掉驱动任务（验收 workaround 是 PYTHONUTF8=1）。

修法：cli 入口 ``_ensure_utf8_stdio`` 把 sys.stdout/stderr reconfigure 成
utf-8 + errors=\"replace\"（进程内等价于 PYTHONUTF8=1），用户无需设环境变量。
"""
import io
import sys
from types import SimpleNamespace

from harness.agent.loop import AgentLoop


class _Recorder:
    def append(self, *args, **kwargs):
        pass

    def record(self, *args, **kwargs):
        pass


def test_record_step_survives_gbk_console(monkeypatch):
    """不设 PYTHONUTF8 的 GBK 控制台：cli 入口 reconfigure 后 _record_step 的 💭 打印
    不再抛 UnicodeEncodeError（验收 Critical ③）。"""
    buf = io.BytesIO()
    out = io.TextIOWrapper(buf, encoding="gbk")        # 模拟 Windows GBK 控制台
    err = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    from harness.cli import _ensure_utf8_stdio
    _ensure_utf8_stdio()
    assert out.encoding.lower().replace("-", "") == "utf8"

    loop = AgentLoop.__new__(AgentLoop)                # 只喂 _record_step 的依赖
    loop.events = _Recorder()
    loop.memory = _Recorder()
    decision = SimpleNamespace(thought="t", say="💭 想想", action="finish", args={})
    loop._record_step(decision)                        # 不抛即过（修复前：UnicodeEncodeError）
    out.flush()
    assert "💭 想想" in buf.getvalue().decode("utf-8")
