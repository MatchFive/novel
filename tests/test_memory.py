"""M3 记忆体系测试：分层提交 + 组装器注入激活（不调用 LLM，构造提取结果直测）。运行：python tests/test_memory.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.llm.assembler import ContextAssembler  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.memory_service import MemoryService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "mem.db")
        db.init_schema()
        seed_defaults(db)

        project = ProjectService(db).create(name="mem", genre="玄幻", logline="故事")
        cs = CharacterService(db)
        hero = cs.create(project_id=project.id, name="林晚", role_type="protagonist",
                         profile={"personality": "外冷内热"})
        villain = cs.create(project_id=project.id, name="玄冥", role_type="antagonist")

        os_ = OutlineService(db)
        arc = os_.create(project_id=project.id, title="第一卷", goal="觉醒")
        chs = ChapterService(db)
        ch = chs.create(project_id=project.id, arc_id=arc.id, title="第一章",
                        objective="林晚遇险", pov_char_id=hero.id)
        chs.update(ch.id, content="林晚在山谷遇袭，玄冥说出真相：「你父亲是我杀的。」")

        svc = MemoryService(db, None)  # 不调用 LLM，直接测提交

        # 模拟 LLM 提取结果
        extracted = {
            "events": [
                {"title": "山谷遇袭", "summary": "林晚在山谷被玄冥袭击，得知父亲死因",
                 "location": "山谷", "characters": ["林晚", "玄冥"],
                 "new_info": ["林晚父亲之死"], "foreshadow": ""}
            ],
            "character_events": [
                {"character": "林晚", "summary": "遭遇袭击，得知父亲是玄冥所杀",
                 "quotes": "你父亲是我杀的。", "acted": "质问玄冥", "felt": "震惊与愤怒",
                 "got_lost": "", "secrets": "决心复仇"},
                {"character": "玄冥", "summary": "袭击林晚并揭露身份",
                 "quotes": "你父亲是我杀的。", "acted": "出手袭击", "felt": "轻蔑",
                 "got_lost": "", "secrets": ""},
            ],
            "info_updates": [
                {"title": "林晚父亲之死", "content": "林晚之父死于玄冥之手",
                 "revealed_to": ["林晚"]}
            ],
            "promise_updates": [
                {"kind": "promise", "content": "林晚发誓为父报仇", "by_character": "林晚",
                 "status": "new", "resolution": ""}
            ],
        }

        svc.commit_extraction(project.id, ch.id, extracted)

        # ---- 验证分层提交 ----
        events = svc.list_events(project.id)
        assert len(events) == 1, len(events)
        assert events[0].char_ids and len(events[0].char_ids) == 2

        hero_events = svc.list_character_events(hero.id)
        assert len(hero_events) == 1
        assert hero_events[0].quotes == "你父亲是我杀的。", "台词原文必须保真"

        items = svc.list_info_items(project.id)
        assert len(items) == 1 and items[0].title == "林晚父亲之死"

        promises = svc.list_promises(project.id)
        assert len(promises) == 1 and promises[0].status == "open"

        ch_after = chs.get(ch.id)
        assert ch_after.memory_status == "extracted"
        print("Memory commit OK: 1 event / 2 char_events / 1 info / 1 promise, quotes preserved")

        # ---- 幂等：重复提交同章不重复 ----
        svc.commit_extraction(project.id, ch.id, extracted)
        assert len(svc.list_events(project.id)) == 1
        assert len(svc.list_character_events(hero.id)) == 1
        print("Idempotent re-commit OK")

        # ---- 组装器注入激活：POV 记忆注入 ----
        asm = ContextAssembler(db)
        ch = chs.get(ch.id)
        bundle = asm.memory.inject_draft_memory(
            project.id, ch, pov_char_id=hero.id, keywords=["父亲", "玄冥"],
        )
        assert "你父亲是我杀的" in bundle.pov_memories, bundle.pov_memories
        assert "复仇" in bundle.pov_memories or "震惊" in bundle.pov_memories
        assert bundle.hits.get("pov_memories", 0) >= 1
        assert bundle.open_promises and "为父报仇" in bundle.open_promises
        assert bundle.knowledge_state and "林晚父亲之死" in bundle.knowledge_state
        print("MemoryInjector activated OK: POV memories + knowledge + promises injected")

        print("ALL MEMORY TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
