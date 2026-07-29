import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.harness.models import HarnessStage, WorkerResult
from app.agents.harness.nodes.responder import (
    GLOBAL_RESPONDER_PROMPT,
    RESPONDER_PROMPT,
    respond,
    respond_state,
)
from app.agents.harness.state import ChangeRecord, HarnessState, HarnessContext


class FakeLLM:
    def __init__(self, reply: str):
        self.chat = AsyncMock(return_value=reply)

    async def chat(self, messages: list) -> str:
        return self.chat(messages)


class TestRespondState(unittest.IsolatedAsyncioTestCase):
    async def test_respond_state_with_project_uses_default_prompt_and_context(self):
        llm = FakeLLM("已为您整理好变更摘要。")
        results = {
            "t1": WorkerResult(
                worker="character",
                task_id="t1",
                changes=[{"action": "add", "fields": {"name": "Alice"}}],
                notes=["added protagonist"],
                stage="execute",
            ),
        }
        state = HarnessState(
            project_id="p1",
            user_input="add a new character",
            results=results,
            context=HarnessContext(
                project_id="p1",
                entities={"characters": [{"id": "c1", "name": "Alice"}]},
            ),
        )
        new_state = await respond_state(state, llm)

        self.assertEqual(new_state.stage, HarnessStage.COMMIT)
        self.assertEqual(new_state.summary, "已为您整理好变更摘要。")
        call_args = llm.chat.await_args
        messages = call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], RESPONDER_PROMPT)
        user_msg = messages[1]["content"]
        self.assertIn("Alice", user_msg)
        self.assertIn("add a new character", user_msg)
        self.assertIn("character", user_msg)

    async def test_respond_state_maps_worker_results_fields(self):
        results = {
            "t1": WorkerResult(
                worker="outline",
                task_id="t1",
                changes=[{"action": "add", "fields": {"title": "Arc"}}],
                notes=["note 1"],
                stage="plan",
            ),
        }
        state = HarnessState(project_id="p1", user_input="plan outline", results=results)

        async def fake_respond(llm, records, *, user_input, history_context, system_prompt, context, worker_results):
            self.assertEqual(worker_results, [
                {
                    "worker": "outline",
                    "stage": "plan",
                    "changes": [{"action": "add", "fields": {"title": "Arc"}}],
                    "notes": ["note 1"],
                }
            ])
            return "ok"

        with patch("app.agents.harness.nodes.responder.respond", fake_respond):
            new_state = await respond_state(state, None)
        self.assertEqual(new_state.summary, "ok")
        self.assertEqual(new_state.stage, HarnessStage.COMMIT)

    async def test_respond_state_without_project_uses_global_prompt(self):
        llm = FakeLLM("这是一个通用回答。")
        state = HarnessState(
            project_id=None,
            user_input="hello",
            results={},
            change_records=[],
        )
        new_state = await respond_state(state, llm)

        self.assertEqual(new_state.stage, HarnessStage.COMMIT)
        self.assertEqual(new_state.summary, "这是一个通用回答。")
        messages = llm.chat.await_args.args[0]
        self.assertEqual(messages[0]["content"], GLOBAL_RESPONDER_PROMPT)
        self.assertIn("hello", messages[1]["content"])

    async def test_respond_state_empty_results_and_records(self):
        llm = FakeLLM("没有需要确认的变更。")
        state = HarnessState(project_id="p1", user_input="what is this?", results={})
        new_state = await respond_state(state, llm)

        self.assertEqual(new_state.stage, HarnessStage.COMMIT)
        self.assertEqual(new_state.summary, "没有需要确认的变更。")
        user_msg = llm.chat.await_args.args[0][1]["content"]
        self.assertIn("（无变更）", user_msg)
        self.assertIn("（无 Worker 输出）", user_msg)


class TestRespond(unittest.IsolatedAsyncioTestCase):
    async def test_respond_returns_llm_reply(self):
        llm = FakeLLM("变更摘要以准备就绪。")
        records = [
            ChangeRecord(
                id="cr_1",
                project_id="p1",
                action="add",
                entity_type="character",
                after={"name": "Alice"},
            )
        ]
        result = await respond(llm, records, user_input="add Alice")
        self.assertEqual(result, "变更摘要以准备就绪。")

    async def test_respond_fallback_on_exception(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm failure"))
        records = [
            ChangeRecord(
                id="cr_1",
                project_id="p1",
                action="add",
                entity_type="character",
                after={"name": "Alice"},
            )
        ]
        result = await respond(llm, records, user_input="add Alice")
        self.assertIn("已生成以下变更建议", result)
        self.assertIn("character", result)


if __name__ == "__main__":
    unittest.main()
