"""数据库备份/恢复（设计文档 9：SQLite 备份）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.constants import BACKUP_DIR, BACKUP_KEEP


def backups_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(db_path: Path, data_dir: Path, keep: int = BACKUP_KEEP) -> Path:
    """用 SQLite backup API 创建一致性备份，并清理超量旧备份。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backups_dir(data_dir) / f"novel_{ts}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # 清理超量
    backups = sorted(backups_dir(data_dir).glob("novel_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def list_backups(data_dir: Path) -> list[Path]:
    return sorted(backups_dir(data_dir).glob("novel_*.db"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """覆盖恢复：调用方需先关闭数据库连接，且恢复后应重启应用。"""
    src = sqlite3.connect(str(backup_path))
    try:
        dst = sqlite3.connect(str(db_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
