"""迁移 AssistantSession 与 UserSetting 表以支持多轮记忆和多 session。"""
from __future__ import annotations

import asyncio

from sqlalchemy import text
from app.database import engine


COLUMNS = {
    "assistant_sessions": [
        ("title", "VARCHAR(255) DEFAULT '未命名对话'"),
        ("is_active", "BOOLEAN DEFAULT 0"),
        ("summaries", "TEXT DEFAULT '[]'"),
        ("message_count", "INTEGER DEFAULT 0"),
    ],
    "user_settings": [
        ("assistant_summary_threshold", "INTEGER DEFAULT 20"),
        ("assistant_max_summaries", "INTEGER DEFAULT 5"),
        ("assistant_summary_max_length", "INTEGER DEFAULT 1000"),
    ],
}


async def migrate() -> None:
    async with engine.begin() as conn:
        for table, cols in COLUMNS.items():
            for name, ddl in cols:
                try:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    print(f"Added {table}.{name}")
                except Exception as e:
                    print(f"Skip {table}.{name}: {e}")
        # 修正现有 session：每个 project 保留第一条为 active
        await conn.execute(text("""
            UPDATE assistant_sessions
            SET is_active = 1
            WHERE id IN (
                SELECT MIN(id) FROM assistant_sessions GROUP BY project_id
            )
        """))
        print("Migration done.")


if __name__ == "__main__":
    asyncio.run(migrate())
