"""outline 域执行 + 章节新增去重测试（不调用 LLM）。运行：python tests/test_outline_command.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        db = Database(Path(tempfile.mkdtemp()) / "oc.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="oc", genre="玄幻", logline="故事")
        svc = CommandService(db, None)
        ol = OutlineService(db)
        chs = ChapterService(db)

        # ---- outline add：新增卷（带目标与概要） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "outline", "action": "add", "target": "第一卷 开荒期",
             "after": "卷末主角在异世界站稳脚跟，拥有可靠领地与盟友。",
             "summary": "主角穿越后在晨曦大陆开荒，从落脚到立足。"}]}, {0})
        assert r["applied"] == 1, r
        arcs = ol.list(project.id)
        assert len(arcs) == 1 and arcs[0].title == "第一卷 开荒期"
        assert "站稳脚跟" in (arcs[0].goal or "")
        print("outline add OK")

        # ---- outline add 去重：同名卷不重复创建，改为更新目标/概要 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "outline", "action": "add", "target": "第一卷 开荒期",
             "after": "卷末主角建成第一个据点。",
             "summary": "修订概要"}]}, {0})
        assert r["applied"] == 1 and "未重复创建" in r["applied_notes"][0], r
        arcs = ol.list(project.id)
        assert len(arcs) == 1 and "据点" in (arcs[0].goal or "")
        print("outline add dedupe OK")

        # ---- outline update：按「第1卷」引用 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "outline", "action": "update", "target": "第1卷",
             "field": "conflict", "after": "与当地领主的土地争端"}]}, {0})
        assert r["applied"] == 1, r
        assert ol.list(project.id)[0].conflict == "与当地领主的土地争端"
        print("outline update OK")

        # ---- chapter add：seq 递增 + 同名去重 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "落脚", "after": "主角抵达荒原"},
            {"domain": "chapter", "action": "add", "target": "落脚", "after": "补充：结识第一位盟友"},
            {"domain": "chapter", "action": "add", "target": "第一桶金", "after": "完成首笔交易"},
        ]}, {0, 1, 2})
        assert r["applied"] == 3, r
        chapters = chs.list(project.id)
        assert len(chapters) == 2, [c.title for c in chapters]  # 同名「落脚」合并
        assert "结识第一位盟友" in (chapters[0].objective or "")
        seqs = [c.seq for c in chapters]
        assert seqs == sorted(seqs) and len(set(seqs)) == 2, seqs  # seq 不重复
        print("chapter add dedupe + seq OK")

        # ---- outline delete：卷下章节脱离但保留 ----
        arc = ol.list(project.id)[0]
        chs.update(chapters[0].id, arc_id=arc.id) if hasattr(chs, "update") else None
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "outline", "action": "delete", "target": "第1卷"}]}, {0})
        assert r["applied"] == 1, r
        assert not ol.list(project.id)
        print("outline delete OK")

        print("ALL OUTLINE COMMAND TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
