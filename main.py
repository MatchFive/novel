"""程序入口：加载配置 → 初始化数据库 → 启动 PySide6 + qasync 事件循环。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import qasync

from app.core.config import load_config
from app.core.constants import DB_FILENAME, PROJECT_ROOT
from app.core.logger import get_logger, setup_logging
from app.db.migrations import seed_defaults
from app.db.session import Database
from app.ui.main_window import MainWindow

log = get_logger("main")


def main() -> int:
    cfg = load_config()

    data_dir = Path(cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir)

    db = Database(data_dir / DB_FILENAME)
    db.init_schema()
    seed_defaults(db)
    log.info("数据库就绪: %s", db.db_path)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.core.constants import ICON_PNG, STYLE_FILE

    app = QApplication(sys.argv)
    app.setApplicationName("NovelStudio")
    app.setOrganizationName("NovelStudio")

    # 视觉风格：图标 + 主题样式
    if ICON_PNG.exists():
        app.setWindowIcon(QIcon(str(ICON_PNG)))
    if STYLE_FILE.exists():
        app.setStyleSheet(STYLE_FILE.read_text(encoding="utf-8"))

    # 启动对话框：选择最近项目 / 新建 / 打开
    from app.services.project_service import ProjectService
    from app.ui.dialogs.start_dialog import StartDialog

    start = StartDialog(ProjectService(db))
    start.exec()
    start_action = start.action
    start_project_id = start.selected_project_id

    if start_action == "open" and start_project_id is not None:
        window = MainWindow(cfg, db, project_id=start_project_id)
        window.show()
    else:
        window = MainWindow(cfg, db)
        window.show()
        if start_action == "new":
            QTimer.singleShot(0, window.on_new_project)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
