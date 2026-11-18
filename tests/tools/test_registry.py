"""ToolRegistry：注册 / 白名单拒绝 / 并行上限与只读约束（失败不吞，归 loop 分类）。"""
import pytest

from harness.run.constants import MAX_PARALLEL, OBS_MAX
from harness.tools.registry import ToolRegistry


def _echo(store, args):
    return f"echo:{args.get('x', '')}"


@pytest.fixture
def registry():
    return ToolRegistry(store=object())


def test_register_and_execute(registry):
    registry.register("echo", _echo, read_only=True)
    assert registry.execute("echo", {"x": "hi"}, allowed=frozenset({"echo"})) == "echo:hi"


def test_duplicate_registration_raises(registry):
    registry.register("echo", _echo, read_only=True)
    with pytest.raises(ValueError):
        registry.register("echo", _echo, read_only=True)


def test_execute_not_in_allowed_returns_refusal(registry):
    registry.register("echo", _echo, read_only=True)
    out = registry.execute("echo", {"x": "hi"}, allowed=frozenset({"other"}))
    assert isinstance(out, str)
    assert "echo" in out  # 拒絕文案带工具名
    assert "白名单" in out
    assert out != "echo:hi"  # 确实没执行


def test_execute_refusal_hints_allowed_set(registry):
    registry.register("echo", _echo, read_only=True)
    out = registry.execute("echo", {}, allowed=frozenset({"other", "more"}))
    assert "other" in out and "more" in out


def test_execute_unknown_tool_raises(registry):
    with pytest.raises(KeyError):
        registry.execute("nope", {}, allowed=frozenset({"nope"}))


def test_execute_handler_exception_not_swallowed(registry):
    def boom(store, args):
        raise RuntimeError("炸了")

    registry.register("boom", boom, read_only=True)
    with pytest.raises(RuntimeError, match="炸了"):
        registry.execute("boom", {}, allowed=frozenset({"boom"}))


def test_execute_trims_observation(registry):
    def huge(store, args):
        return "长" * (OBS_MAX * 3)

    registry.register("huge", huge, read_only=True)
    out = registry.execute("huge", {}, allowed=frozenset({"huge"}))
    assert len(out) <= OBS_MAX
    assert "截断" in out


def test_execute_parallel_runs_all_read_only(registry):
    registry.register("echo", _echo, read_only=True)
    calls = [("echo", {"x": str(i)}) for i in range(3)]
    out = registry.execute_parallel(calls, allowed=frozenset({"echo"}))
    assert out == ["echo:0", "echo:1", "echo:2"]


def test_execute_parallel_over_cap_refuses_each(registry):
    registry.register("echo", _echo, read_only=True)
    calls = [("echo", {"x": str(i)}) for i in range(MAX_PARALLEL + 1)]
    out = registry.execute_parallel(calls, allowed=frozenset({"echo"}))
    assert len(out) == MAX_PARALLEL + 1
    assert all("并行" in s or "白名单" in s for s in out)  # 每调用一条拒绝串，不 raise
    assert all(s != "echo:0" for s in out)


def test_execute_parallel_non_read_only_refused_per_call(registry):
    def write_tool(store, args):
        return "written"

    registry.register("echo", _echo, read_only=True)
    registry.register("write_tool", write_tool, read_only=False)
    out = registry.execute_parallel(
        [("echo", {"x": "a"}), ("write_tool", {})], allowed=frozenset({"echo", "write_tool"})
    )
    assert out[0] == "echo:a"
    assert "write_tool" in out[1] and "write_tool" != out[1]


def test_execute_parallel_not_in_allowed_refused_per_call(registry):
    registry.register("echo", _echo, read_only=True)
    out = registry.execute_parallel(
        [("echo", {"x": "a"}), ("echo", {"x": "b"})], allowed=frozenset({"echo"})
    )
    # 两个都在 allowed：正常执行；再把第二个踢出 allowed → 只拒第二个
    assert out == ["echo:a", "echo:b"]
    out = registry.execute_parallel(
        [("echo", {"x": "a"}), ("echo", {"x": "b"})], allowed=frozenset()
    )
    assert all("白名单" in s for s in out)


def test_execute_parallel_unknown_tool_error_string(registry):
    out = registry.execute_parallel([("ghost", {})], allowed=frozenset({"ghost"}))
    assert len(out) == 1
    assert "ghost" in out[0]


def test_execute_parallel_handler_exception_reported_per_call(registry, caplog):
    """handler 抛异常 → **就地**记成该条观测（带工具名 + ``FAILED:`` 前缀），**不穿出批次**。

    2026-11-15 独立审核 L2：此前任一调用抛异常 → 异常穿出整个批次 → loop 只知道
    「这批失败了」、**不知道是哪个工具失败** → 熔断账记在**第一个**工具头上 →
    真正失败的那个没被冷却、没失败的那个被误冷却。

    → 现在逐条兜底：观测里看得见**工具名 + 失败标签**，且**其他调用照常执行**
    （不因一条炸了连坐）。

    **观测里不带异常原文**（2026-11-17 独立复审 P17-3）：原文常含**瞬时词**
    （「连接被拒绝」「超时」）→ loop 的分类会把这条**确定性失败**翻成 transient →
    永远进不了失败账 → 死循环重试同一个坏工具；原文也可能带路径等内部细节。
    → 原文进**日志**，观测只给确定性标签。
    """
    def boom(store, args):
        raise RuntimeError("炸了")

    registry.register("echo", _echo, read_only=True)
    registry.register("boom", boom, read_only=True)

    out = registry.execute_parallel(
        [("echo", {"x": "a"}), ("boom", {})], allowed=frozenset({"echo", "boom"}))

    assert len(out) == 2
    assert out[0] == "echo:a"                       # 前面那条照常执行（不连坐）
    assert out[1].startswith("FAILED: boom ")      # 工具名在观测里
    assert "调用失败" in out[1]                     # 确定性标签
    assert "炸了" not in out[1]                     # **原文不上屏**（瞬时词/内部细节）
    # 原文进日志（可查）
    assert any("炸了" in r.message for r in caplog.records)


def test_execute_parallel_exception_does_not_stop_later_calls(registry):
    """一条炸了 → **后面的调用照常执行**（逐条兜底，不是整批放弃）。"""
    def boom(store, args):
        raise ValueError("炸了")

    registry.register("boom", boom, read_only=True)
    registry.register("echo", _echo, read_only=True)

    out = registry.execute_parallel(
        [("boom", {}), ("echo", {"x": "z"})], allowed=frozenset({"boom", "echo"}))

    assert out[1] == "echo:z"
    assert out[0].startswith("FAILED: boom ")


def test_execute_parallel_observation_has_no_transient_word(registry, caplog):
    """异常消息里的**瞬时词**不得把观测变成 transient（G10：观测禁含瞬时词）。

    「连接被拒绝」「超时」都在 ``TRANSIENT_MARKERS`` 里 → 若原样进观测，
    loop 会把这条**确定性失败**判成 transient → 失败的工具永远进不了冷却账。
    """
    from harness.agent.loop import AgentLoop
    from harness.run import constants

    def boom(store, args):
        raise ConnectionError("连接被拒绝")

    registry.register("boom", boom, read_only=True)
    out = registry.execute_parallel([("boom", {})], allowed=frozenset({"boom"}))

    markers = constants.TRANSIENT_MARKERS + ("truncated", "starved")
    assert not any(m in out[0] for m in markers), f"观测含瞬时词：{out[0]}"
    assert AgentLoop._classify(out[0]) == "failed"   # 确定性失败，不许被翻成 transient
