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

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "novel.db"

MIGRATIONS: list[tuple[str, str, str]] = [
    ("long_outlines", "type", "TEXT DEFAULT 'broad'"),
    ("long_plot_nodes", "chapter_id", "CHAR(36)"),
    ("long_plot_nodes", "order", "INTEGER DEFAULT 0"),
    ("long_chapters", "detailed_outline", "TEXT DEFAULT ''"),
    ("long_chapters", "status", "TEXT DEFAULT 'draft'"),
    ("assistant_sessions", "context", "TEXT DEFAULT '{}'"),
    ("model_configs", "level", "TEXT"),
    ("model_configs", "embedding_model", "TEXT"),
]


def migrate(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path}; nothing to migrate.")
        return

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    for table, column, dtype in MIGRATIONS:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {dtype}")
            print(f"Added column {table}.{column}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                print(f"Column {table}.{column} already exists, skipping")
            else:
                raise

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
