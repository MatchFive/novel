"""LangGraph 三条核心能力验证：interrupt / checkpointer / 条件边。运行：python tests/test_langgraph_features.py"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


class FakeLLM:
    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        user = kwargs.get("user_prompt", "")
        if "任务编排" in user:
            return {"tasks": [
                {"entity": "character", "action": "add", "description": "新增角色伊维娜",
                 "target": "伊维娜", "order": 1},
            ]}
        if "【角色】" in user and "新增角色" in user:
            return {"changes": [{"domain": "character", "action": "add", "target": "伊维娜",
                                  "profile": {"personality": "优雅神秘"}}]}
        return {}


def main() -> int:
    try:
        cfg = load_config()
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "novel.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="lg", genre="玄幻", logline="story")

        from app.workflows.agent_orchestrator import Orchestrator

        # ---- 主场景：run/resume 在同一事件循环（应用内 qasync 单 loop） ----
        async def main_flow():
            orch = Orchestrator(db, FakeLLM(), project.id,
                                state_db_path=str(tmp / "workflow_state.db"))

            # interrupt：run 到确认点暂停
            result = await orch.run("生成奇迹女神-伊维娜的角色卡", history=[])
            assert result.interrupted, "应在 confirm 节点 interrupt 暂停"
            assert result.thread_id, "应有 thread_id 供续跑"
            assert result.plan.get("changes"), "应已生成修改计划"
            print("interrupt OK: paused at confirm, thread_id =", result.thread_id[:8], flush=True)

            # checkpointer：状态落盘
            state_db = tmp / "workflow_state.db"
            assert state_db.exists() and state_db.stat().st_size > 0
            print("checkpointer OK: workflow_state.db written", flush=True)

            # resume 确认执行（同一 loop）
            applied = await orch.resume(result.thread_id, True, [0])
            assert applied.applied is not None
            assert applied.applied.get("applied") == 1, applied.applied
            print("resume->apply OK: 伊维娜 created", flush=True)

            # 历史回放
            history = await orch.get_history(result.thread_id)
            assert history, "应有图状态历史"
            print(f"history replay OK: {len(history)} checkpoints", flush=True)
            return orch

        asyncio.run(main_flow())
        chars = CharacterService(db).list(project.id)
        assert any(c.name == "伊维娜" for c in chars), "确认后应创建伊维娜"
        print("character persisted OK", flush=True)

        # ---- 条件边：clarification 时不执行（独立 loop，独立编排器） ----
        class ClarifyLLM(FakeLLM):
            async def generate_structured(self, **kwargs):
                user = kwargs.get("user_prompt", "")
                if "任务编排" in user:
                    return {"clarification": "请问要修改哪个实体？", "tasks": []}
                return {}

        async def clarify_flow():
            orch2 = Orchestrator(db, ClarifyLLM(), project.id,
                                 state_db_path=str(tmp / "workflow_state2.db"))
            result2 = await orch2.run("随便改改", history=[])
            assert not result2.interrupted, "clarification 应直接 END 不暂停"
            assert result2.plan.get("clarification"), "应返回澄清问题"
            assert not result2.plan.get("changes"), "澄清时不应有变更"
            print("conditional edge OK: clarification -> END (no execute)", flush=True)

        asyncio.run(clarify_flow())

        print("ALL LANGGRAPH FEATURE TESTS PASSED", flush=True)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
