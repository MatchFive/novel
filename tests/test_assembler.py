"""M2 上下文组装器 / FTS5 检索 单元测试（不调用 LLM）。运行：python tests/test_assembler.py"""
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
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "asm.db")
        db.init_schema()
        seed_defaults(db)

        project = ProjectService(db).create(name="asm", genre="玄幻", logline="故事",
                                            style_guide="网文爽快流")
        # 设定
        ws = WorldService(db)
        cat = ws.list_categories(project.id)[0]
        ws.create_entry(project_id=project.id, category_id=cat.id,
                        title="灵气复苏", content="灵气在 2049 年复苏，人类觉醒异能。",
                        tags="核心规则", importance=5, canonical=True)
        ws.create_entry(project_id=project.id, category_id=cat.id,
                        title="星火城", content="东方大陆第一大城市，城墙由黑曜石筑成。",
                        importance=4)

        # 角色
        cs = CharacterService(db)
        hero = cs.create(project_id=project.id, name="林晚", role_type="protagonist",
                         profile={"personality": "外冷内热", "speech_style": "寡言"})

        # 卷 + 章
        os_ = OutlineService(db)
        arc = os_.create(project_id=project.id, title="第一卷", goal="觉醒血脉",
                         conflict="宗门追杀", resolution="逃离宗门")
        chs = ChapterService(db)
        ch = chs.create(project_id=project.id, arc_id=arc.id, title="第一章",
                        objective="林晚在灵气复苏中觉醒", pov_char_id=hero.id)
        chs.update(ch.id, content="林晚站在星火城的城墙上，望着远方的灵气潮汐。")
        chs.set_beats(ch.id, [{"pov": "林晚", "location": "星火城", "event": "目睹灵气潮汐"}])
        ch = chs.get(ch.id)

        # ---- FTS5 检索 ----
        hits = ws.list_entries(project.id, None)
        assert len(hits) == 2
        found = [e for e in hits if e.title == "灵气复苏"]
        assert found and found[0].canonical
        print("FTS5 tables created OK (2 entries)")

        # ---- 组装器 ----
        asm = ContextAssembler(db)
        gen = dict(cfg.gen_config)
        result = asm.build_draft_prompt(
            project_id=project.id, chapter=ch, gen_config=gen,
            recent_draft="林晚站在星火城的城墙上。",
            target_beat="目睹灵气潮汐",
            recent_summaries="[第1章] 林晚觉醒。",
            memory=asm.memory.inject_draft_memory(
                project_id=project.id, chapter=ch, pov_char_id=hero.id,
                keywords=["星火城", "灵气"],
            ),
        )

        assert "林晚" in result.user_prompt, "应注入出场角色"
        assert "灵气复苏" in result.user_prompt or "星火城" in result.user_prompt, "应注入世界观检索命中"
        assert "【生成配置】" in result.system_prompt
        assert "三条记忆铁律" in result.user_prompt, "应包含记忆铁律指令"
        assert result.injected.get("kept_blocks"), "应记录保留区块"
        assert result.injected.get("world_entries", 0) >= 1, "FTS 应命中世界观条目"
        print("Assembler OK: user_prompt chars =", len(result.user_prompt),
              "| injected =", {k: v for k, v in result.injected.items() if k != "kept_blocks"})

        # ---- 记忆注入（M3 数据为空时安全） ----
        bundle = asm.memory.inject_draft_memory(project.id, ch, pov_char_id=hero.id)
        assert bundle.pov_memories == ""  # 尚无 character_events
        print("MemoryInjector safe-empty OK")

        print("ALL ASSEMBLER TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
