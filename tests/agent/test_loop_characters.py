"""P40c：角色工具走 loop await 分支——跨事件循环楔死修复钉。

draft_character/update_character/delete_character/build_characters 的 async 核心
若经 ``_sync_tool``（asyncio.to_thread → registry 同步 handler → ``_run_sync``），
LLM client（httpx 连接池属主事件循环）被跨循环复用 → 调用挂死至 600s 超时
（2026-11-20 实跑复现：draft_character 首调挂 600s，其后即时 api:api）。

与 write_plot 同纪律（Task 11 接缝）：loop 按名字永远走 ``_async_tool`` await 分支。
钉法：FakeLLM.complete 记录调用线程 id，主循环线程 id 预存，断言二者相等——
若回归 _sync_tool 的 to_thread，线程断言立刻翻红。
"""
import asyncio
import json
import threading

from test_loop_p3 import FakeLLM, make_settings  # 复用 P3 脚手架

from harness.agent.loop import AgentLoop
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.constants import WORK_MEMORY_REPLAY
from harness.run.events import EventLog
from harness.run.memory import WorkMemory
from harness.run.state import RunState
from harness.store.project import ProjectStore
from harness.tools import readonly_registry
from harness.tools.characters import register_character_tools

MAIN_TID = threading.get_ident()


class TidLLM(FakeLLM):
    """在 complete 里记录线程 id（FakeLLM 子类，脚本消费逻辑原样继承）。"""

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192,
                       thinking=False):
        self.calls.append({"model": model, "temperature": temperature,
                           "prompt": messages[0]["content"],
                           "tid": threading.get_ident()})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict) and "tool_calls" in item:
            from test_loop_p3 import _FakeCompletion
            return _FakeCompletion(text=item.get("content", ""),
                                   tool_calls=item["tool_calls"], usage=self._usage)
        from test_loop_p3 import _FakeCompletion
        return _FakeCompletion(text=item, usage=self._usage)


CARD_JSON = json.dumps({"name": "甲", "role_type": "protagonist", "aliases": ["阿甲"],
                        "profile": {"age": "二十"}, "arc": "成长"}, ensure_ascii=False)


def _call(tool, **args):
    return {"tool_calls": [{"name": tool, "arguments": json.dumps(args, ensure_ascii=False)}]}


def _finish():
    return {"tool_calls": [{"name": "finish",
                            "arguments": json.dumps({"summary": "完成"})}]}


def _tool_results(events, tool):
    return [e for e in events.tail(200) if e["kind"] == "tool_result"
            and e["payload"].get("tool") == tool]


def make_char_loop(store, script):
    llm = TidLLM(script)
    settings = make_settings()
    prompts = PromptLoader(home_dir=store.root)
    ui = ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    registry = readonly_registry(store)
    register_character_tools(registry, store=store, llm=llm, prompts=prompts,
                             settings=settings, ui=ui)
    events = EventLog(store.root)
    state = RunState.new("测试 brief：建卡", ["characters"])
    memory = WorkMemory.rebuild(events.tail(WORK_MEMORY_REPLAY * 2))
    loop = AgentLoop(store=store, state=state, events=events, memory=memory,
                     registry=registry, ui=ui, llm=llm, prompts=prompts,
                     deliverables=["characters"], settings=settings)
    return loop, llm, events, state


def test_draft_character_runs_in_main_loop_not_worker_thread(tmp_path):
    """draft_character 经 _async_tool await（线程 id == 主循环线程）且产出卡草稿观测。"""
    runner_tid = threading.get_ident()   # conftest 可能在worker线程跑测试：以本函数线程为准
    info = create_project(tmp_path, "角色书")
    store = ProjectStore(info.path)
    script = [_call("draft_character", name="甲", role_type="protagonist"),
              CARD_JSON, _finish()]
    loop, llm, events, state = make_char_loop(store, script)
    result = asyncio.run(loop.run())
    assert result.status == "done"
    obs = _tool_results(events, "draft_character")
    assert len(obs) == 1, "draft_character 未走 await 分支或观测未落"
    assert "甲" in obs[0]["payload"]["observation"]
    assert not obs[0]["payload"]["observation"].startswith("FAILED")
    # 核心钉：角色起草的 LLM 调用（路由头 "tier: pro" 标识 task.auto_character_create）
    # 必须发生在事件循环线程——回归 to_thread 即翻红（跨循环复用连接池会楔死）。
    draft_calls = [c for c in llm.calls if "tier: pro" in c["prompt"]]
    assert draft_calls, "未捕获到角色起草的 LLM 调用"
    assert all(c["tid"] == runner_tid for c in draft_calls), \
        f"角色起草逃逸到工作线程: {[c['tid'] for c in draft_calls]} != {runner_tid}"
