"""Schema migration script for Novel Studio.

Run after upgrading the codebase on an existing database:

    cd backend && python scripts/migrate.py

It adds columns introduced by the four-feature refactor using SQLite's
`ALTER TABLE ADD COLUMN`. New databases created by SQLAlchemy already have
these columns, so running this script is idempotent and safe.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "novel.db"

MIGRATIONS: list[tuple[str, str, str]] = [
    ("long_outlines", "type", "TEXT DEFAULT 'broad'"),
    ("long_plot_nodes", "chapter_id", "CHAR(36)"),
    ("long_plot_nodes", "order", "INTEGER DEFAULT 0"),
    ("long_chapters", "detailed_outline", "TEXT DEFAULT ''"),
    ("long_chapters", "status", "TEXT DEFAULT 'draft'"),
    ("assistant_sessions", "context", "TEXT DEFAULT '{}'"),
    ("model_configs", "level", "TEXT"),
    ("model_configs", "embedding_model", "TEXT"),
    ("model_configs", "embedding_dimension", "INTEGER DEFAULT 1536"),
    ("user_settings", "assistant_history_recent_messages", "INTEGER DEFAULT 20"),
    ("user_settings", "assistant_history_top_k", "INTEGER DEFAULT 5"),
    ("user_settings", "content_rating", "VARCHAR(16) DEFAULT 'standard'"),
    ("user_settings", "chapter_target_words", "INTEGER DEFAULT 2500"),
    ("long_change_records", "source", "VARCHAR(16) DEFAULT 'staged'"),
]

CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS assistant_summary_embeddings (
        id CHAR(36) PRIMARY KEY,
        session_id CHAR(36) NOT NULL,
        turn_range VARCHAR(32) NOT NULL,
        summary_text TEXT NOT NULL,
        embedding BLOB NOT NULL,
        model VARCHAR(128) NOT NULL,
        dimension INTEGER NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_assistant_summary_embeddings_session_id ON assistant_summary_embeddings(session_id)",
    "DROP TABLE IF EXISTS message_embeddings",
]


def migrate(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path}; nothing to migrate.")
        return

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    for table, column, dtype in MIGRATIONS:
        try:
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {dtype}')
            print(f"Added column {table}.{column}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                print(f"Column {table}.{column} already exists, skipping")
            else:
                raise

    for sql in CREATE_TABLES:
        cur.execute(sql)
        print(f"Executed: {sql.strip()[:60]}...")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
