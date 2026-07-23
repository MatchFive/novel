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
    ("long_outlines", "chapter_start", "INTEGER"),
    ("long_outlines", "chapter_end", "INTEGER"),
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

# Order matters: drop short-fiction tables first (they reference projects), then
# remove sessions tied to short projects, then the short projects themselves, and
# finally any sessions whose project no longer exists. Each step is wrapped in
# a broad OperationalError handler so a cleanup failure cannot block startup.
CLEANUP_SQL = [
    "DROP TABLE IF EXISTS short_settings",
    "DROP TABLE IF EXISTS short_chapters",
    "DROP TABLE IF EXISTS short_hotspots",
    "DELETE FROM assistant_summary_embeddings WHERE session_id IN (SELECT id FROM assistant_sessions WHERE project_id IN (SELECT id FROM projects WHERE type = 'short'))",
    "DELETE FROM assistant_messages WHERE session_id IN (SELECT id FROM assistant_sessions WHERE project_id IN (SELECT id FROM projects WHERE type = 'short'))",
    "DELETE FROM assistant_sessions WHERE project_id IN (SELECT id FROM projects WHERE type = 'short')",
    "DELETE FROM projects WHERE type = 'short'",
    "DELETE FROM assistant_sessions WHERE project_id IS NOT NULL AND project_id NOT IN (SELECT id FROM projects)",
]


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _remove_hotspot_sources_column(cur: sqlite3.Cursor) -> bool:
    """Recreate user_settings without the deprecated hotspot_sources column.

    SQLite does not support ALTER TABLE DROP COLUMN, so we copy the data to a
    new table, drop the old one, and rename. Safe to run repeatedly: it only
    executes when hotspot_sources still exists.
    """
    columns = _table_columns(cur, "user_settings")
    if "hotspot_sources" not in columns:
        print("user_settings.hotspot_sources already removed")
        return False

    if not columns:
        print("user_settings table not found; skipping hotspot_sources cleanup")
        return False

    cur.executescript(
        """
        CREATE TABLE user_settings_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recursive_limit INTEGER DEFAULT 8,
            theme TEXT DEFAULT 'light',
            assistant_summary_threshold INTEGER DEFAULT 20,
            assistant_max_summaries INTEGER DEFAULT 5,
            assistant_summary_max_length INTEGER DEFAULT 1000,
            assistant_history_recent_messages INTEGER DEFAULT 20,
            assistant_history_top_k INTEGER DEFAULT 5,
            content_rating TEXT DEFAULT 'standard',
            chapter_target_words INTEGER DEFAULT 2500
        );
        INSERT INTO user_settings_new (
            id, recursive_limit, theme, assistant_summary_threshold,
            assistant_max_summaries, assistant_summary_max_length,
            assistant_history_recent_messages, assistant_history_top_k,
            content_rating, chapter_target_words
        )
        SELECT
            id, recursive_limit, theme, assistant_summary_threshold,
            assistant_max_summaries, assistant_summary_max_length,
            assistant_history_recent_messages, assistant_history_top_k,
            content_rating, chapter_target_words
        FROM user_settings;
        DROP TABLE user_settings;
        ALTER TABLE user_settings_new RENAME TO user_settings;
        """
    )
    print("Recreated user_settings table without hotspot_sources")
    return True


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

    try:
        _remove_hotspot_sources_column(cur)
    except sqlite3.OperationalError as exc:
        print(f"Warning: failed to remove user_settings.hotspot_sources: {exc}")

    for sql in CLEANUP_SQL:
        try:
            cur.execute(sql)
            print(f"Executed cleanup: {sql.strip()[:60]}...")
        except sqlite3.OperationalError as exc:
            print(f"Warning: cleanup step skipped due to error: {exc}")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
