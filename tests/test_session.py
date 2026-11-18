"""W0 session 层：RunSession / start_run / Runner 协议的接线测试。

repl.py 的 run 启动知识搬到 UI 无关的 ``harness.session``：``start_run`` 做
单 run 守卫（RunBusyError）→ provider 检查（None）→ 落盘新 run → runner.launch。
**零 print**（UI 无关）——提示文案留在 repl。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.session import Components, RunBusyError, RunSession, start_run
from harness.store.project import ProjectStore


class FakeRunner:
    """记录 launch 入参的 Runner 桩；alive() 可预设。"""

    def __init__(self, alive: bool = False):
        self._alive = alive
        self.launches: list[tuple] = []

    def alive(self) -> bool:
        return self._alive

    def launch(self, session: RunSession, state, comp: Components,
               *, intake: tuple[str, list[str], bool] | None = None) -> None:
        self.launches.append((session, state, comp, intake))


def _settings(tmp_path: Path, *, with_provider: bool) -> Settings:
    providers = {}
    default_provider = ""
    if with_provider:
        providers = {"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                            lite="m", standard="m", pro="m")}
        default_provider = "test"
    return Settings(library_root=tmp_path / "lib", providers=providers,
                    default_provider=default_provider, default_tier="standard",
                    web_verify=True)


def _session(tmp_path: Path, *, with_provider: bool) -> tuple[RunSession, ProjectStore]:
    info = create_project(tmp_path / "lib", "大道朝天")
    store = ProjectStore(info.path)
    return RunSession(info, store, _settings(tmp_path, with_provider=with_provider)), store


def test_start_run_busy_raises(tmp_path):
    """runner.alive()=True → RunBusyError（不起新 run、不落 state）。"""
    sess, store = _session(tmp_path, with_provider=True)
    runner = FakeRunner(alive=True)

    with pytest.raises(RunBusyError):
        start_run(sess, runner, "写个故事", ["outline"])

    assert runner.launches == []
    assert not (store.root / ".harness" / "state.json").exists()


def test_start_run_no_provider_returns_none(tmp_path):
    """空 settings（provider 缺）→ None，且**不落** state.json / run_started。"""
    sess, store = _session(tmp_path, with_provider=False)
    runner = FakeRunner()

    assert start_run(sess, runner, "写个故事", ["outline"]) is None

    assert runner.launches == []
    assert not (store.root / ".harness" / "state.json").exists()
    assert sess.events.tail(100) == []


def test_start_run_happy_path(tmp_path, monkeypatch):
    """state.json 落盘 + run_started 事件 + runner.launch 被调用（入参记录）。"""
    sess, store = _session(tmp_path, with_provider=True)
    sentinel = Components(llm=object(), registry=object(), ui=object())
    monkeypatch.setattr(RunSession, "components", lambda self: sentinel)
    runner = FakeRunner()

    state = start_run(sess, runner, "写个故事", ["outline"], user_specified=True)

    assert state is not None
    assert (store.root / ".harness" / "state.json").exists()
    started = [e for e in sess.events.tail(100) if e["kind"] == "run_started"]
    assert len(started) == 1
    assert started[0]["payload"] == {"raw": "写个故事", "deliverables": ["outline"]}
    # launch 收到的正是本 session / 新 state / components / intake 三元组
    assert runner.launches == [(sess, state, sentinel, ("写个故事", ["outline"], True))]
    # 会话上下文已就位（驱动前的新 run 语义）
    assert sess.state is state
    assert sess.memory is not None


def test_repl_has_no_session_knowledge():
    """repl.py 不再持有 session 知识（纯委托）：无 _make_components / _Session 定义。"""
    from harness import repl

    src = inspect.getsource(repl)
    assert "_make_components" not in src
    assert "class _Session" not in src


# ------------------------------------------------------------------ 终审 Finding ①
# abort/pause 必须把挂起中的文件协议审批停掉（写取消标记），否则停放的 waiter 永不
# 返回 → 驱动任务永远活着 → 全局忙守卫（runner.alive()）永不释放，全项目 409 busy。

def test_abort_run_cancels_parked_file_approval(tmp_path):
    """abort 时 .harness/pending.md 在（审批挂起中）→ 写 pending.answer 取消标记。"""
    from harness.session import abort_run

    sess, store = _session(tmp_path, with_provider=True)
    sess.new_run("写个故事", ["outline"])
    base = store.root / ".harness"
    base.mkdir(exist_ok=True)
    (base / "pending.md").write_text("# 待审批\n", encoding="utf-8")

    abort_run(sess)

    assert (base / "pending.answer").read_text(encoding="utf-8") == "c\n"


def test_pause_run_cancels_parked_file_approval(tmp_path):
    """pause 同口径：挂起中的文件审批一并取消（pause 后 run 不会再读这个答案）。"""
    from harness.session import pause_run

    sess, store = _session(tmp_path, with_provider=True)
    sess.new_run("写个故事", ["outline"])
    base = store.root / ".harness"
    base.mkdir(exist_ok=True)
    (base / "pending.md").write_text("# 待审批\n", encoding="utf-8")

    pause_run(sess, "user /pause")

    assert (base / "pending.answer").read_text(encoding="utf-8") == "c\n"


def test_abort_run_without_pending_writes_nothing(tmp_path):
    """没有 pending.md（不在等审批）→ 不得凭空写 pending.answer（无副作用）。"""
    from harness.session import abort_run

    sess, store = _session(tmp_path, with_provider=True)
    sess.new_run("写个故事", ["outline"])

    abort_run(sess)

    assert not (store.root / ".harness" / "pending.answer").exists()


def test_abort_during_pending_approval_releases_drive_task(tmp_path):
    """端到端复现网（终审 Finding ①）：run 挂在审批上时 abort → 停放的 waiter 收到
    取消标记、ApprovalCancelled 上抛、驱动任务结束、runner.alive() 转 False。

    修复前：abort 只翻状态，没人写取消标记 → waiter 永久停放 → alive() 恒 True
    （全项目 409 busy 直到重启）。
    """
    import asyncio
    import json
    import threading

    from harness.prompts.loader import PromptLoader
    from harness.run.approval import ApprovalUI
    from harness.session import abort_run
    from harness.tools import readonly_registry, register_modify_tools, register_outline_tools
    from harness.web.runner import AsyncRunner

    class _FakeCompletion:
        def __init__(self, text):
            self.text = text
            self.usage = None
            self.tool_calls = None

    class _FakeLLM:
        def __init__(self, script):
            self._script = list(script)
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m", standard="standard-model", pro="pro-model")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192,
                           thinking=False):
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return _FakeCompletion(item)

        async def aclose(self):
            pass

    settings = _settings(tmp_path, with_provider=True)
    info = create_project(tmp_path / "lib", "终审书")
    store = ProjectStore(info.path)
    sess = RunSession(info, store, settings, force_file_ui=True)
    base = store.root / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0.01)
    llm = _FakeLLM([
        json.dumps({"thought": "需要用户拍板", "say": "请示",
                    "action": "ask_user",
                    "args": {"question": "选哪个？", "options": ["甲", "乙"]}},
                   ensure_ascii=False),
    ])
    prompts = PromptLoader(home_dir=store.root)
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    comp = Components(llm=llm, registry=registry, ui=ui)
    runner = AsyncRunner()

    async def main():
        state = sess.new_run("写个故事", ["outline"])
        runner.launch(sess, state, comp)
        for _ in range(400):
            if (base / "pending.md").exists():
                break
            await asyncio.sleep(0.005)
        assert (base / "pending.md").exists()        # run 已挂起在审批上
        abort_run(sess)                              # 用户终止：状态翻转 + 取消标记
        for _ in range(600):
            if not runner.alive():
                break
            await asyncio.sleep(0.01)
        assert not runner.alive()                    # 关键断言：驱动任务真的结束了
        assert sess.state.status == "aborted"

    done = threading.Event()
    failure: list[BaseException] = []

    def driver():
        try:
            asyncio.run(main())
        except BaseException as exc:  # noqa: BLE001 —— 原样抛回主线程断言
            failure.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=driver, daemon=True)
    thread.start()
    assert done.wait(timeout=30), "abort 未释放挂起的审批：驱动任务仍在停放（Finding ① 复现）"
    thread.join(timeout=5)
    if failure:
        raise failure[0]
