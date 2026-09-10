"""只测编排器 _ensure_graph。"""
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
        return {}


async def m():
    cfg = load_config()
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "n.db")
    db.init_schema()
    seed_defaults(db)
    p = ProjectService(db).create(name="t", genre="玄幻", logline="s")
    from app.workflows.agent_orchestrator import Orchestrator
    orch = Orchestrator(db, FakeLLM(), p.id, state_db_path=str(tmp / "ws.db"))
    print("before ensure", flush=True)
    await orch._ensure_graph()
    print("graph built", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(m(), 20))
    except Exception:
        traceback.print_exc()
