"""HarnessRuntime: state-machine driver for the assistant harness."""
from __future__ import annotations

import logging

from app.agents.harness.models import HarnessError, HarnessStage
from app.agents.harness.nodes.aggregator import aggregate_state
from app.core.errors import AppError
from app.agents.harness.state import HarnessState
from app.agents.harness.nodes.analyze import analyze
from app.agents.harness.nodes.commit import commit_state
from app.agents.harness.nodes.executor import executor
from app.agents.harness.nodes.responder import respond_state
from app.agents.harness.nodes.supervisor import supervisor
from app.agents.harness.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


class HarnessRuntime:
    def __init__(
        self,
        state: HarnessState,
        manager: WorkerManager,
        db,
        llm_factory,
        settings,
        recent_messages: list,
        is_global: bool,
        recursive_limit: int,
    ):
        self.state = state
        self.manager = manager
        self.db = db
        self.llm_factory = llm_factory
        self.settings = settings
        self.recent_messages = recent_messages
        self.is_global = is_global
        self.recursive_limit = recursive_limit

    async def run(self) -> HarnessState:
        while self.state.stage not in (HarnessStage.DONE, HarnessStage.ERROR):
            await self.step()
        return self.state

    async def step(self) -> HarnessState:
        try:
            if self.state.stage == HarnessStage.INIT:
                self.state.stage = HarnessStage.ANALYZE
            elif self.state.stage == HarnessStage.ANALYZE:
                self.state = await analyze(self.state, self.db, self.settings, self.recent_messages)
            elif self.state.stage == HarnessStage.PLAN:
                llm = await self.llm_factory("medium")
                self.state = await supervisor(self.state, llm, self.manager)
            elif self.state.stage == HarnessStage.EXECUTE:
                history_context = self.state.context.session_context.get("history_context")
                self.state = await executor(
                    self.state,
                    self.db,
                    self.llm_factory,
                    self.recursive_limit,
                    history_context=history_context,
                )
            elif self.state.stage == HarnessStage.AGGREGATE:
                self.state = aggregate_state(self.state)
            elif self.state.stage == HarnessStage.RESPOND:
                llm = await self.llm_factory("low")
                history_context = self.state.context.session_context.get("history_context")
                self.state = await respond_state(self.state, llm, history_context=history_context)
            elif self.state.stage == HarnessStage.COMMIT:
                self.state = await commit_state(self.state, self.db, self.is_global)
            else:
                self.state.error = HarnessError(stage=self.state.stage, message=f"未知阶段: {self.state.stage}")
                self.state.stage = HarnessStage.ERROR
        except AppError:
            logger.exception("Harness runtime error at stage %s", self.state.stage)
            raise
        except Exception as e:
            logger.exception("Harness runtime error at stage %s", self.state.stage)
            self.state.error = HarnessError(stage=self.state.stage, message=str(e), details={"type": type(e).__name__})
            self.state.stage = HarnessStage.ERROR
        return self.state
