"""正文生成工作流测试（LangGraph：上下文构造→写作→审核→修正）。运行：python tests/test_draft_workflow.py"""
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
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


class FakeLLM:
    """记录 review 调用次数，按序返回 pass/fail。"""

    def __init__(self, review_results: list[dict]):
        self.review_results = list(review_results)
        self.calls: list[str] = []

    def load_prompt(self, key, project_id=None, **kwargs):
        return f"[{key}]"

    async def generate_stream(self, **kwargs):
        self.calls.append(f"stream:{kwargs.get('prompt_key')}")
        yield "这是一段正文。林晚在星火城觉醒。"

    async def generate_structured(self, **kwargs):
        self.calls.append(f"structured:{kwargs.get('prompt_key')}")
        if kwargs.get("prompt_key") == "task.draft_review":
            return self.review_results.pop(0) if self.review_results else {"pass": True, "issues": []}
        return {}


def _setup():
    cfg = load_config()
    db = Database(Path(tempfile.mkdtemp()) / "dw.db")
    db.init_schema()
    seed_defaults(db)
    p = ProjectService(db).create(name="dw", genre="玄幻", logline="story")
    hero = CharacterService(db).create(project_id=p.id, name="林晚", role_type="protagonist")
    WorldService(db).create_entry(project_id=p.id, category_id=None, title="星火城",
                                  content="东方大陆第一城。", canonical=True)
    arc = OutlineService(db).create(project_id=p.id, title="第一卷")
    ch = ChapterService(db).create(project_id=p.id, arc_id=arc.id, title="第一章",
                                   objective="觉醒", pov_char_id=hero.id)
    return db, p, ch


def main() -> int:
    try:
        db, p, ch = _setup()
        from app.workflows.draft_workflow import DraftWorkflow

        # ---- 场景1：审核一次通过 ----
        wf = DraftWorkflow(db, FakeLLM([{"pass": True, "issues": []}]))
        r = asyncio.run(wf.run(project_id=p.id, chapter=ch, gen_config={}, target_beat="觉醒"))
        assert r.draft and "林晚" in r.draft
        assert r.pass_review is True and r.revise_count == 0
        print("scenario 1 (pass first review) OK: draft generated, no revise")

        # ---- 场景2：第一次审核不过 → 修正 → 第二次通过 ----
        wf2 = DraftWorkflow(db, FakeLLM([
            {"pass": False, "issues": [{"type": "setting", "issue": "与设定冲突", "suggestion": "改X"}]},
            {"pass": True, "issues": []},
        ]))
        r2 = asyncio.run(wf2.run(project_id=p.id, chapter=ch, gen_config={}, target_beat="觉醒"))
        assert r2.pass_review is True and r2.revise_count == 1, (r2.pass_review, r2.revise_count)
        print("scenario 2 (review fail -> revise -> pass) OK: revised once")

        # ---- 场景3：审核始终不过 → 修正 2 次后结束（防死循环） ----
        wf3 = DraftWorkflow(db, FakeLLM([
            {"pass": False, "issues": [{"type": "logic", "issue": "逻辑问题", "suggestion": "改Y"}]},
            {"pass": False, "issues": [{"type": "logic", "issue": "逻辑问题", "suggestion": "改Y"}]},
            {"pass": False, "issues": [{"type": "logic", "issue": "逻辑问题", "suggestion": "改Y"}]},
            {"pass": False, "issues": [{"type": "logic", "issue": "逻辑问题", "suggestion": "改Y"}]},
        ]))
        r3 = asyncio.run(wf3.run(project_id=p.id, chapter=ch, gen_config={}, target_beat="觉醒"))
        assert r3.revise_count == 2, f"应修正 2 次后结束，实际 {r3.revise_count}"
        print("scenario 3 (max revise 2, no infinite loop) OK")

        # ---- 上下文构造注入（角色记忆/世界观进入 system/user prompt） ----
        assert r.injected, "应有注入统计"
        print("context collection OK (injected:", {k: v for k, v in r.injected.items() if k != 'kept_blocks'}, ")")

        print("ALL DRAFT WORKFLOW TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
