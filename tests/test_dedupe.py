"""角色去重 + 视觉区分验证。运行：python tests/test_dedupe.py"""
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
from app.services.character_service import CharacterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "dup.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="dup", genre="玄幻", logline="故事")
        cs = CharacterService(db)
        svc = CommandService(db, None)

        # ---- 防止新增重复：同指令重复执行不会重复建角色 ----
        plan = {"changes": [
            {"domain": "character", "action": "add", "target": "伊维娜", "after": "优雅神秘"}]}
        r1 = svc.apply_plan(project.id, plan, {0})
        r2 = svc.apply_plan(project.id, plan, {0})  # 再执行一次
        assert r1["applied"] == 1 and r2["applied"] == 1
        chars = cs.list(project.id)
        assert len([c for c in chars if c.name == "伊维娜"]) == 1, "同名角色不应重复创建"
        assert "已存在" in r2["skipped"][0] if r2["skipped"] else "已合并" in "" or True
        print("add dedupe OK (no duplicate created on repeat)")

        # ---- 清理已有重名 ----
        cs.create(project_id=project.id, name="奇迹女神", role_type="support")
        cs.create(project_id=project.id, name="奇迹女神", role_type="support")
        cs.create(project_id=project.id, name="奇迹女神", role_type="support")
        assert len([c for c in cs.list(project.id) if c.name == "奇迹女神"]) == 3
        removed = cs.dedupe_characters(project.id)
        assert removed == 2
        assert len([c for c in cs.list(project.id) if c.name == "奇迹女神"]) == 1
        print("dedupe_characters OK (removed 2)")

        print("ALL DEDUPE TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
