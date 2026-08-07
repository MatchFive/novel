"""启动时初始化默认模型预设（仅在 model_configs 为空时执行）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelConfig


DEFAULT_MODEL_PRESETS: list[dict[str, str | float]] = [
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "temperature": 0.7},
]


async def seed_default_models(db: AsyncSession) -> None:
    """如果数据库里没有任何模型配置，则写入 DeepSeek 预设（api_key 为空，标记为默认）。"""
    res = await db.execute(select(ModelConfig))
    if res.scalars().first():
        return
    for preset in DEFAULT_MODEL_PRESETS:
        db.add(ModelConfig(
            name=preset["name"],
            base_url=preset["base_url"],
            api_key="",
            model=preset["model"],
            temperature=preset.get("temperature"),
            is_default=True,
        ))
    await db.commit()
