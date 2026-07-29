"""Tests for the harness runtime state-machine driver."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.harness.models import HarnessError, HarnessStage
from app.agents.harness.runtime import HarnessRuntime
from app.agents.harness.state import HarnessState


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def _make_runtime(stage: HarnessStage = HarnessStage.INIT) -> tuple[HarnessRuntime, MagicMock, AsyncMock, MagicMock]:
    state = HarnessState(stage=stage, user_input="test input", session_id="s1")
    manager = MagicMock()
    db = MagicMock()
    llm_factory = AsyncMock(return_value="llm-instance")
    settings = MagicMock()
    recent_messages = [{"role": "user", "content": "hi"}]
    runtime = HarnessRuntime(
        state=state,
        manager=manager,
        db=db,
        llm_factory=llm_factory,
        settings=settings,
        recent_messages=recent_messages,
        is_global=False,
        recursive_limit=8,
    )
    return runtime, manager, llm_factory, db


def _advance_to(stage: HarnessStage):
    def _side_effect(*args, **kwargs):
        # The first positional argument is the state in all node functions.
        state = args[0]
        state.stage = stage
        return state

    return _side_effect


@pytest.mark.anyio
async def test_run_advances_through_all_stages_and_ends_done():
    with (
        patch("app.agents.harness.runtime.analyze", new_callable=AsyncMock) as mock_analyze,
        patch("app.agents.harness.runtime.supervisor", new_callable=AsyncMock) as mock_supervisor,
        patch("app.agents.harness.runtime.executor", new_callable=AsyncMock) as mock_executor,
        patch("app.agents.harness.runtime.aggregate_state") as mock_aggregate,
        patch("app.agents.harness.runtime.respond_state", new_callable=AsyncMock) as mock_respond,
        patch("app.agents.harness.runtime.commit_state", new_callable=AsyncMock) as mock_commit,
    ):
        mock_analyze.side_effect = _advance_to(HarnessStage.PLAN)
        mock_supervisor.side_effect = _advance_to(HarnessStage.EXECUTE)
        mock_executor.side_effect = _advance_to(HarnessStage.AGGREGATE)
        mock_aggregate.side_effect = _advance_to(HarnessStage.RESPOND)
        mock_respond.side_effect = _advance_to(HarnessStage.COMMIT)
        mock_commit.side_effect = _advance_to(HarnessStage.DONE)

        runtime, manager, llm_factory, db = _make_runtime(HarnessStage.INIT)

        final_state = await runtime.run()

        assert final_state.stage == HarnessStage.DONE
        assert final_state.error is None

        # INIT does not invoke a node; it just advances to ANALYZE.
        assert mock_analyze.await_count == 1
        assert mock_supervisor.await_count == 1
        assert mock_executor.await_count == 1
        assert mock_aggregate.call_count == 1
        assert mock_respond.await_count == 1
        assert mock_commit.await_count == 1

        # Validate arguments passed through the runtime.
        analyze_call = mock_analyze.await_args
        assert analyze_call.args[0] is runtime.state
        assert analyze_call.args[1] is db
        assert analyze_call.args[2] is runtime.settings
        assert analyze_call.args[3] is runtime.recent_messages

        supervisor_call = mock_supervisor.await_args
        assert supervisor_call.args[0] is runtime.state
        assert supervisor_call.args[1] == "llm-instance"
        assert supervisor_call.args[2] is manager

        executor_call = mock_executor.await_args
        assert executor_call.args[0] is runtime.state
        assert executor_call.args[1] is db
        assert executor_call.args[2] is llm_factory
        assert executor_call.args[3] == 8
        assert executor_call.kwargs == {"history_context": None}

        commit_call = mock_commit.await_args
        assert commit_call.args[0] is runtime.state
        assert commit_call.args[1] is db
        assert commit_call.args[2] is False  # is_global

        llm_factory.assert_awaited()
        assert llm_factory.await_args_list[0].args == ("medium",)
        assert llm_factory.await_args_list[1].args == ("low",)


@pytest.mark.anyio
async def test_run_stops_immediately_when_done():
    runtime, _, _, _ = _make_runtime(HarnessStage.DONE)
    final = await runtime.run()
    assert final.stage == HarnessStage.DONE


@pytest.mark.anyio
async def test_run_stops_immediately_when_error():
    runtime, _, _, _ = _make_runtime(HarnessStage.ERROR)
    runtime.state.error = HarnessError(stage=HarnessStage.COMMIT, message="prior failure")
    final = await runtime.run()
    assert final.stage == HarnessStage.ERROR
    assert final.error.message == "prior failure"


@pytest.mark.anyio
async def test_run_captures_exception_and_transitions_to_error():
    with patch("app.agents.harness.runtime.commit_state", new_callable=AsyncMock) as mock_commit:
        mock_commit.side_effect = ValueError("commit blew up")

        runtime, _, _, db = _make_runtime(HarnessStage.COMMIT)
        final = await runtime.run()

        assert final.stage == HarnessStage.ERROR
        assert final.error is not None
        assert "commit blew up" in final.error.message
        assert final.error.details["type"] == "ValueError"
        assert final.error.stage == HarnessStage.COMMIT


@pytest.mark.anyio
async def test_step_transitions_one_stage_at_a_time():
    with patch("app.agents.harness.runtime.analyze", new_callable=AsyncMock) as mock_analyze:
        mock_analyze.side_effect = _advance_to(HarnessStage.PLAN)

        runtime, _, _, _ = _make_runtime(HarnessStage.INIT)

        state_after_init_step = await runtime.step()
        assert state_after_init_step.stage == HarnessStage.ANALYZE
        mock_analyze.assert_not_awaited()

        state_after_analyze = await runtime.step()
        assert state_after_analyze.stage == HarnessStage.PLAN
        mock_analyze.assert_awaited_once()


@pytest.mark.anyio
async def test_run_uses_global_and_recursive_limits():
    with (
        patch("app.agents.harness.runtime.executor", new_callable=AsyncMock) as mock_executor,
        patch("app.agents.harness.runtime.commit_state", new_callable=AsyncMock) as mock_commit,
    ):
        mock_executor.side_effect = _advance_to(HarnessStage.AGGREGATE)
        mock_commit.side_effect = _advance_to(HarnessStage.DONE)

        state = HarnessState(stage=HarnessStage.EXECUTE, user_input="global", session_id="s2")
        runtime = HarnessRuntime(
            state=state,
            manager=MagicMock(),
            db=MagicMock(),
            llm_factory=AsyncMock(),
            settings=MagicMock(),
            recent_messages=[],
            is_global=True,
            recursive_limit=42,
        )

        final = await runtime.run()
        assert final.stage == HarnessStage.DONE
        assert mock_executor.await_args.args[3] == 42
        assert mock_commit.await_args.args[2] is True
