"""Agent 编排器测试（LangGraph，mock LLM 验证节点执行顺序与实体分发）。运行：python tests/test_orchestrator.py"""
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
from app.services.project_service import ProjectService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


class FakeLLM:
    """模拟 LLMService：按提示词内容返回预设结果，验证编排逻辑（不调真实 LLM）。"""

    def __init__(self):
        self.captured: list[str] = []  # 记录每次调用的 user_prompt（验证结果传递）

    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        user = kwargs.get("user_prompt", "")
        self.captured.append(user)
        if "任务编排" in user:
            return {"tasks": [
                {"entity": "character", "action": "update", "description": "改角色性格", "order": 2},
                {"entity": "world", "action": "update", "description": "改世界观设定", "order": 1},
            ]}
        if "世界观设定" in user:
            return {"changes": [{"domain": "world", "target": "星火城", "after": "新设定内容"}]}
        if "【角色】" in user and "你只负责【角色】域" in user:
            return {"changes": [{"domain": "character", "target": "林晚",
                                  "field": "profile.personality", "after": "新性格"}]}
        return {}


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "orch.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="orch", genre="玄幻", logline="故事")
        CharacterService(db).create(project_id=project.id, name="林晚")
        WorldService(db).create_entry(project_id=project.id, category_id=None,
                                      title="星火城", content="东方大陆第一城。")

        from app.workflows.agent_orchestrator import Orchestrator
        fake = FakeLLM()
        orch = Orchestrator(db, fake, project.id)
        result = asyncio.run(orch.run("把林晚性格改强，并更新星火城的设定"))

        # 验证分解：world 任务 order=1 排在 character 前
        assert len(result.tasks) == 2, result.tasks
        assert result.tasks[0]["entity"] == "world" and result.tasks[0]["order"] == 1
        assert result.tasks[1]["entity"] == "character" and result.tasks[1]["order"] == 2

        # 验证按顺序执行并汇总
        changes = result.plan.get("changes", [])
        assert len(changes) == 2, changes
        assert changes[0]["domain"] == "world"      # world 先执行（order=1）
        assert changes[1]["domain"] == "character"  # character 后执行
        assert result.plan["impact_summary"]
        print("Orchestrator OK: tasks ordered (world -> character), changes aggregated")

        # 验证结果传递：character Agent 的上下文应包含 world Agent 已生成的变更
        char_call = next((c for c in fake.captured if "你只负责【角色】域" in c), None)
        assert char_call is not None
        assert "前面任务已生成的变更" in char_call, "后续 Agent 应看到前面任务的结果"
        assert "星火城" in char_call and "新设定内容" in char_call, "应包含前面 world 变更内容"
        print("Results propagation OK: later agent sees earlier results")

        print("ALL ORCHESTRATOR TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
