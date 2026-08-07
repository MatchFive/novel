"""迁移 Project 表，增加 writing_style / generation_config JSON 字段。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
backend_dir = script_dir.parent
repo_root = backend_dir.parent
for d in (str(backend_dir), str(repo_root)):
    if d not in sys.path:
        sys.path.insert(0, d)

import sqlalchemy
from sqlalchemy import inspect, text
from app.database import engine


async def migrate():
    async with engine.begin() as conn:
        def _add_columns(sync_conn):
            cols = inspect(sync_conn).get_columns("projects")
            names = {c["name"] for c in cols}
            if "writing_style" not in names:
                sync_conn.execute(text('ALTER TABLE projects ADD COLUMN writing_style TEXT DEFAULT "{}"'))
            if "generation_config" not in names:
                sync_conn.execute(text('ALTER TABLE projects ADD COLUMN generation_config TEXT DEFAULT "{}"'))
            cols2 = inspect(sync_conn).get_columns("model_configs")
            if "temperature" not in {c["name"] for c in cols2}:
                sync_conn.execute(text('ALTER TABLE model_configs ADD COLUMN temperature FLOAT DEFAULT NULL'))
        await conn.run_sync(_add_columns)


if __name__ == "__main__":
    asyncio.run(migrate())
