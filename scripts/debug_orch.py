"""分步调试编排器节点。"""
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


class FakeLLM:
    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        user = kwargs.get("user_prompt", "")
        if "任务编排" in user:
            return {"tasks": [{"entity": "character", "action": "add",
                               "description": "新增角色", "target": "伊维娜", "order": 1}]}
        return {"changes": [{"domain": "character", "action": "add", "target": "伊维娜",
                             "profile": {"personality": "x"}}]}


async def main():
    def log(*a):
        print(*a, flush=True)
    cfg = load_config()
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "n.db")
    db.init_schema()
    seed_defaults(db)
    p = ProjectService(db).create(name="t", genre="玄幻", logline="s")
    from app.workflows.agent_orchestrator import Orchestrator
    orch = Orchestrator(db, FakeLLM(), p.id, state_db_path=str(tmp / "ws.db"))
    log("step1: ensure_graph")
    await orch._ensure_graph()
    log("step1 done: graph built")

    # 单独测 orchestrate 节点（不经图）
    log("step2: orchestrate node alone")
    st = await orch._orchestrate({"command": "生成奇迹女神角色卡", "history": []})
    log("step2 done: tasks =", st.get("tasks"))

    # 单独测 execute 节点
    log("step3: execute node alone")
    st2 = await orch._execute_tasks(st)
    log("step3 done: results =", len(st2.get("results", [])))

    # 单独测 summarize + confirm（confirm 里 interrupt 会暂停——单独调用会 raise）
    log("step4: summarize node alone")
    st3 = await orch._summarize(st2)
    log("step4 done: plan changes =", len(st3.get("plan", {}).get("changes", [])))

    log("ALL STEPS OK")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), 25))
    except Exception:
        traceback.print_exc()
