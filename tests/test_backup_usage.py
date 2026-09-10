"""备份/恢复 与 Token 用量统计测试。运行：python tests/test_backup_usage.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.backup import create_backup, list_backups, restore_backup  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.dashboard_service import DashboardService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "novel.db")
        db.init_schema()
        seed_defaults(db)
        svc = ProjectService(db)
        p = svc.create(name="备份测试", genre="玄幻")
        assert len(svc.list()) == 1

        # ---- 备份 ----
        target = create_backup(db.db_path, tmp)
        assert target.exists() and target.stat().st_size > 0
        print("Backup OK:", target.name)

        # ---- 恢复 ----
        db.engine.dispose()
        restore_backup(target, db.db_path)
        db2 = Database(db.db_path)
        svc2 = ProjectService(db2)
        assert len(svc2.list()) == 1 and svc2.list()[0].name == "备份测试"
        print("Restore OK")

        # ---- Token 用量统计（直接写 generation_logs 模拟） ----
        from app.db.repositories.generation_log_repo import GenerationLogRepository
        with db2.session_ctx() as s:
            GenerationLogRepository(s).add_log(
                project_id=p.id, module="draft", provider="deepseek",
                tier="standard", model="deepseek-chat", prompt_key="task.draft_continue",
                in_tokens=1200, out_tokens=800,
            )
            GenerationLogRepository(s).add_log(
                project_id=p.id, module="memory", provider="deepseek",
                tier="lite", model="deepseek-chat", prompt_key="task.memory_extract",
                in_tokens=2000, out_tokens=500,
            )
            s.commit()
        usage = DashboardService(db2).usage(p.id)
        assert len(usage) == 2
        total_in = sum(u["in_tokens"] for u in usage)
        total_out = sum(u["out_tokens"] for u in usage)
        assert total_in == 3200 and total_out == 1300
        print("Usage stats OK:", usage)

        print("ALL BACKUP/USAGE TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
