"""M4 修订与沉淀测试：一致性扫描问题入库 / 设定沉淀候选管理（不调用 LLM）。运行：python tests/test_m4.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.canon_capture_service import CanonCaptureService  # noqa: E402
from app.services.check_service import CheckService  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "m4.db")
        db.init_schema()
        seed_defaults(db)

        project = ProjectService(db).create(name="m4", genre="玄幻", logline="故事")
        os_ = OutlineService(db)
        arc = os_.create(project_id=project.id, title="第一卷")
        chs = ChapterService(db)
        ch = chs.create(project_id=project.id, arc_id=arc.id, title="第一章")
        chs.update(ch.id, content="这是第一章正文，主角在星火城觉醒。")

        ws = WorldService(db)
        ws.create_entry(project_id=project.id, category_id=None, title="星火城",
                        content="东方大陆第一城。", importance=4, canonical=True)

        # ---- 设定沉淀：候选 → 接受/拒绝 ----
        canon = CanonCaptureService(db, None)
        # 直接构造候选（绕过 LLM）
        from app.db.repositories.canon_repo import ProposedEntryRepository
        with db.session_ctx() as s:
            from app.db.unit_of_work import UnitOfWork
            with UnitOfWork(s) as uow:
                repo = ProposedEntryRepository(s)
                c1 = repo.create(project_id=project.id, source_module="draft",
                                 source_chapter=ch.id, title="黑曜石城墙",
                                 content="星火城城墙由黑曜石筑成。", category="地理", importance=3)
                c2 = repo.create(project_id=project.id, source_module="draft",
                                 source_chapter=ch.id, title="灵气潮汐",
                                 content="每年冬至出现。", category="规则/体系", importance=4)
                s.flush()  # 取得候选 id
                c1_id, c2_id = c1.id, c2.id

        pending = canon.list_pending(project.id)
        assert len(pending) == 2

        # 接受 c1 → 进设定库
        new_id = canon.accept(c1_id, canonical=True)
        assert new_id is not None
        entries = ws.list_entries(project.id, None)
        assert any(e.title == "黑曜石城墙" and e.canonical for e in entries)
        # 拒绝 c2
        canon.reject(c2_id)
        assert len(canon.list_pending(project.id)) == 0
        print("CanonCapture OK: accept->world_entries, reject->gone")

        # ---- 一致性扫描：批切分 / 问题归一化 / 入库与状态 ----
        check = CheckService(db, None)
        chs2 = chs.create(project_id=project.id, arc_id=arc.id, title="第二章")
        chs2 = chs.get(chs2.id)
        chs.update(chs2.id, content="第二章正文，星火城保卫战。")
        chapters = chs.list(project.id)
        batches = CheckService._split_batches(chapters, 6000)
        assert len(batches) == 1 and "第一章正文" in batches[0][1] and "第二章正文" in batches[0][1]

        normalized = CheckService._normalize_issues(
            {"issues": [
                {"chapter": 1, "quote": "xxx", "type": "timeline",
                 "issue": "时间线矛盾", "suggestion": "改为 X"},
                {"chapter": 99, "quote": "y", "type": "bad_type",
                 "issue": "未知类型归为 setting", "suggestion": ""},
            ]}, chapters,
        )
        assert normalized[0]["issue_type"] == "timeline"
        assert normalized[0]["chapter_id"] is not None
        assert normalized[1]["issue_type"] == "setting"
        print("CheckService normalize/split OK")

        # 入库 + 状态流转
        check._save_issues(project.id, "scan001", normalized)
        issues = check.list_issues(project.id, "pending")
        assert len(issues) == 2
        check.set_issue_status(issues[0].id, "resolved")
        assert len(check.list_issues(project.id, "pending")) == 1
        assert len(check.list_issues(project.id, "resolved")) == 1
        print("CheckService issue persistence + status OK")

        print("ALL M4 TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
