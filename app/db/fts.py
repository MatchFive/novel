"""FTS5 全文检索：虚拟表创建与重建（设计文档 6.4 检索注入）。

表结构 DDL 在本文件（app/db 包内，属数据库层）；业务检索在 repositories 内执行。
使用 trigram tokenizer，支持中文子串匹配（SQLite >= 3.34）。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.session import Database

log = logging.getLogger("novel_studio.db.fts")

# 各内容表的 FTS5 外部内容表定义（content='表名' + 触发器保持同步）
FTS_TABLES = {
    "world_entries_fts": {
        "source": "world_entries",
        "columns": "title, content, tags",
    },
    "story_events_fts": {
        "source": "story_events",
        "columns": "title, summary, location, time_marker",
    },
    "character_events_fts": {
        "source": "character_events",
        "columns": "summary, quotes, acted, felt, got_lost, secrets",
    },
    "handbook_chunks_fts": {
        "source": "handbook_chunks",
        "columns": "title, content",
    },
}


def create_fts_objects(db: Database) -> None:
    """创建 FTS5 虚拟表与同步触发器（幂等；仅本文件允许裸 DDL）。"""
    with db.engine.begin() as conn:
        for fts_name, spec in FTS_TABLES.items():
            source = spec["source"]
            cols = spec["columns"]
            conn.execute(text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts_name} "
                f"USING fts5({cols}, content='{source}', content_rowid='id', tokenize='trigram')"
            ))
            # 同步触发器（同名触发器不存在才创建）
            for trig, cond, action in (
                ("_ai", "INSERT", f"INSERT INTO {fts_name}(rowid, {cols}) VALUES (new.id, new.{', new.'.join(cols.split(', '))})"),
                ("_ad", "DELETE", f"INSERT INTO {fts_name}({fts_name}, rowid, {cols}) VALUES('delete', old.id, old.{', old.'.join(cols.split(', '))})"),
                ("_au", "UPDATE", f"INSERT INTO {fts_name}({fts_name}, rowid, {cols}) VALUES('delete', old.id, old.{', old.'.join(cols.split(', '))}); "
                                  f"INSERT INTO {fts_name}(rowid, {cols}) VALUES (new.id, new.{', new.'.join(cols.split(', '))})"),
            ):
                trig_name = f"{source}{trig}"
                conn.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {trig_name} AFTER {cond} ON {source} BEGIN {action}; END"
                ))
        # 首次全量重建（幂等；外部内容表建表后索引为空）
        rebuild_fts(db)


def rebuild_fts(db: Database) -> None:
    """全量重建所有 FTS 索引（外部内容表专用 'rebuild' 命令）。"""
    with db.engine.begin() as conn:
        for fts_name in FTS_TABLES:
            try:
                conn.execute(text(f"INSERT INTO {fts_name}({fts_name}) VALUES('rebuild')"))
            except Exception:
                log.warning("rebuild %s 失败（可能表尚不存在或为空）", fts_name)
