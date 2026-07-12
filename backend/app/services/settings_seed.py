"""启动时初始化默认模型预设（仅在 model_configs 为空时执行）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelConfig


DEFAULT_MODEL_PRESETS: list[dict[str, str]] = [
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    {"name": "Moonshot", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
]


async def seed_default_models(db: AsyncSession) -> None:
    """如果数据库里没有任何模型配置，则写入常用预设（api_key 为空，不标记为默认）。"""
    res = await db.execute(select(ModelConfig))
    if res.scalars().first():
        return
    for preset in DEFAULT_MODEL_PRESETS:
        db.add(ModelConfig(
            name=preset["name"],
            base_url=preset["base_url"],
            api_key="",
            model=preset["model"],
            is_default=False,
        ))
    await db.commit()
