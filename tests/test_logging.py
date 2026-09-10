"""日志系统测试：调用留痕新字段 / 迁移补列 / 日志视图（不调用 LLM）。运行：python tests/test_logging.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "log.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="log", genre="玄幻", logline="story")

        # ---- 迁移补列：generation_logs 新字段可用 ----
        from app.db.repositories.generation_log_repo import GenerationLogRepository
        with db.session_ctx() as s:
            GenerationLogRepository(s).add_log(
                project_id=project.id, module="draft", caller="ChapterView._ai_continue",
                provider="deepseek", tier="standard", model="deepseek-chat",
                prompt_key="task.draft_continue",
                input_summary="【风格指南】网文流……", output_summary="林晚站在城墙上……",
                in_tokens=1200, out_tokens=800, duration_ms=3500, status="ok",
            )
            GenerationLogRepository(s).add_log(
                project_id=project.id, module="memory", caller="MemoryService",
                provider="deepseek", tier="lite", model="deepseek-chat",
                prompt_key="task.memory_extract", in_tokens=2000, out_tokens=0,
                status="failed", error="TimeoutError: connect timeout",
            )
            s.commit()
        with db.session_ctx() as s:
            logs = GenerationLogRepository(s).list_by_project(project.id)
            failed = GenerationLogRepository(s).list_by_project(project.id, "failed")
        assert len(logs) == 2 and len(failed) == 1
        assert failed[0].error.startswith("TimeoutError")
        assert logs[0].caller or logs[1].caller  # caller 字段已记录
        print("generation_logs new fields OK (caller/duration/summary/error)")

        # ---- 日志视图构造 ----
        from app.ui.views.log_view import LogView
        from app.ui.workspace import Workspace
        ws = Workspace(cfg, db, project)
        lv = ws._views["logs"]
        lv.refresh()
        assert hasattr(lv, "calls_list") and hasattr(lv, "date_combo")
        print("LogView OK (file-based calls list + date picker)")

        # ---- 文件日志：按天 JSONL ----
        from app.llm.call_log import read_llm_calls, write_llm_call
        write_llm_call(cfg.data_dir, {
            "module": "draft", "caller": "ChapterView._ai_continue", "provider": "deepseek",
            "tier": "standard", "model": "deepseek-chat", "prompt_key": "task.draft_continue",
            "input_summary": "输入…", "output_summary": "输出正文…", "in_tokens": 1200,
            "out_tokens": 800, "duration_ms": 3200, "status": "ok",
        })
        write_llm_call(cfg.data_dir, {
            "module": "memory", "caller": "MemoryService", "provider": "deepseek",
            "tier": "lite", "model": "deepseek-chat", "prompt_key": "task.memory_extract",
            "in_tokens": 2000, "out_tokens": 0, "duration_ms": 0, "status": "failed",
            "error": "TimeoutError: connect timeout",
        })
        from app.llm.call_log import list_llm_log_files
        files = list_llm_log_files(cfg.data_dir)
        assert files, "应生成按天的日志文件"
        rows = read_llm_calls(files[0])
        assert len(rows) == 2
        failed_rows = read_llm_calls(files[0], status_filter="failed")
        assert len(failed_rows) == 1 and failed_rows[0]["error"].startswith("TimeoutError")
        print("file-based LLM call log OK (daily JSONL + status filter)")

        print("ALL LOGGING TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
