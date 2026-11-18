"""Task 21 验收 Critical ① 回归网：审批挂起不再占死事件循环（2026-11-18 web UI 验收）。

文件协议等待改 async（``ApprovalUI.ask_async`` / ``wait_for_answer_async`` 用
``asyncio.sleep`` 轮询）——web 活 run 挂起审批时，``POST /approvals/answer`` 与
SSE 流保持响应（修复前：同步 ``time.sleep`` 轮询把 uvicorn 事件循环整个冻住，
run 的等待冻住自己的出路）。
"""
import asyncio
import json
import threading
from pathlib import Path

import pytest

from harness.agent.loop import AgentLoop
from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, ApprovalUI
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools import readonly_registry, register_modify_tools, register_outline_tools

# ------------------------------------------------------------------ ApprovalUI 级

def test_ask_async_file_wait_does_not_block_loop(tmp_path):
    """文件协议等待改 async 后：挂起期间事件循环照常推进别的任务（验收 Critical ①）。"""
    base = tmp_path / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0.02)

    async def main():
        progress = 0

        async def ticker():
            nonlocal progress
            while True:
                await asyncio.sleep(0.005)
                progress += 1

        ask_task = asyncio.create_task(ui.ask_async("是否确认当前规划并进入执行？",
                                                    ["确认规划", "需要修改"]))
        ticker_task = asyncio.create_task(ticker())
        try:
            for _ in range(200):
                if (base / "pending.md").exists():
                    break
                await asyncio.sleep(0.005)
            assert (base / "pending.md").exists()      # 挂起：pending.md 已落地
            progress_at_suspend = progress
            await asyncio.sleep(0.05)
            assert progress > progress_at_suspend      # 关键断言：等待没有占死 loop
            (base / "pending.answer").write_text("1\n", encoding="utf-8")
            choice = await asyncio.wait_for(ask_task, timeout=5)
            assert choice.indices == [0]
            assert progress >= 3
            # 收尾纪律：pending 两文件已清（与同步路径同一 finally）
            assert not (base / "pending.md").exists()
            assert not (base / "pending.answer").exists()
        finally:
            ticker_task.cancel()

    asyncio.run(main())


def test_ask_async_cancel_via_file(tmp_path):
    """async 等待路径的取消语义与同步一致：pending.answer 写 c → ApprovalCancelled。"""
    base = tmp_path / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0.02)

    async def main():
        ask_task = asyncio.create_task(ui.ask_async("继续？", ["确认", "取消"]))
        for _ in range(200):
            if (base / "pending.md").exists():
                break
            await asyncio.sleep(0.005)
        (base / "pending.answer").write_text("c\n", encoding="utf-8")
        with pytest.raises(ApprovalCancelled):
            await asyncio.wait_for(ask_task, timeout=5)

    asyncio.run(main())


# ------------------------------------------------------------------ loop 级（drive_run 同款循环）

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


def _make_loop(store, script, ui):
    settings = Settings(library_root=Path("x"),
                        providers={"test": ProviderConfig(base_url="https://api.test",
                                                          api_key="k", lite="m",
                                                          standard="standard-model",
                                                          pro="pro-model")},
                        default_provider="test", default_tier="standard", web_verify=True)
    llm = _FakeLLM(script)
    prompts = PromptLoader(home_dir=store.root)
    registry = readonly_registry(store)
    register_outline_tools(registry, store=store, llm=llm, prompts=prompts, settings=settings)
    register_modify_tools(registry, store=store, llm=llm, prompts=prompts, ui=ui)
    events = EventLog(store.root)
    state = RunState.new("测试 brief", ["outline"])
    memory = WorkMemory()
    loop = AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=["outline"], settings=settings)
    return loop, state


def test_loop_ask_user_suspends_without_blocking_loop(tmp_path, capsys):
    """run 挂在 ask_user 审批时，同一事件循环上的并发任务持续推进（Critical ① 端到端）。

    缺陷未修时同步 ``time.sleep`` 轮询会把**整个事件循环**冻住——测试代码自己也没法跑，
    所以整段 main() 丢进一个带看门狗的线程：20 秒内跑不完即「循环被占死」判定失败。
    """
    info = create_project(tmp_path, "async审批书")
    store = ProjectStore(info.path)
    base = store.root / ".harness"
    ui = ApprovalUI(non_tty_path=base, poll_interval=0.02)
    ask_step = json.dumps({"thought": "需要用户拍板", "say": "请示",
                           "action": "ask_user",
                           "args": {"question": "选哪个？", "options": ["甲", "乙"]}},
                          ensure_ascii=False)
    finish_step = json.dumps({"thought": "已答", "say": "收工", "action": "finish",
                              "args": {"summary": "完成"}}, ensure_ascii=False)
    loop, state = _make_loop(store, [ask_step, finish_step], ui)

    async def main():
        progress = 0

        async def ticker():
            nonlocal progress
            while True:
                await asyncio.sleep(0.005)
                progress += 1

        run_task = asyncio.create_task(loop.run())
        ticker_task = asyncio.create_task(ticker())
        try:
            for _ in range(400):
                if (base / "pending.md").exists():
                    break
                await asyncio.sleep(0.005)
            assert (base / "pending.md").exists()      # run 已挂起在审批上
            progress_at_suspend = progress
            await asyncio.sleep(0.05)
            assert progress > progress_at_suspend      # loop 未被占死
            (base / "pending.answer").write_text("2\n", encoding="utf-8")
            result = await asyncio.wait_for(run_task, timeout=10)
            assert result.status == "done" and state.status == "done"
            assert progress >= 3
        finally:
            ticker_task.cancel()
            if not run_task.done():
                run_task.cancel()

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
    assert done.wait(timeout=20), "事件循环被审批等待占死（Critical ① 复现：挂起期间零进展）"
    thread.join(timeout=5)
    if failure:
        raise failure[0]
    assert "💭 收工" in capsys.readouterr().out        # run 真走到 finish
