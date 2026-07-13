"""根据用户模型配置构造 LLMClient。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.llm_client import LLMClient
from app.models import ModelConfig


async def get_llm_client(db: AsyncSession, level: str | None = None) -> LLMClient:
    """按重要度选择模型配置。

    选择顺序：
    1. 如果传入 level，先查找对应 level 的 ModelConfig。
    2. 没有则查找 is_default=True 的全局默认配置。
    3. 还没有则回退到 .env 默认（LLMClient()）。
    """
    cfg = None
    if level:
        res = await db.execute(select(ModelConfig).where(ModelConfig.level == level))  # noqa: E712
        cfg = res.scalars().first()
    if not cfg:
        res = await db.execute(select(ModelConfig).where(ModelConfig.is_default == True))  # noqa: E712
        cfg = res.scalars().first()
    if cfg:
        return LLMClient(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or None,
            model=cfg.model or None,
        )
    return LLMClient()


# 保留原兼容别名，避免现有调用点立即报错。
async def get_default_llm_client(db: AsyncSession) -> LLMClient:
    """优先使用用户设置的默认模型配置；没有则回退到 .env 默认。"""
    return await get_llm_client(db, level=None)


async def get_embedding_model_name(db: AsyncSession) -> str:
    """返回应使用的 embedding 模型名。

    选择顺序：
    1. level="embedding" 的 ModelConfig 的 `model` 字段。
    2. 全局默认 ModelConfig 的 `embedding_model` 字段。
    3. app.config.settings.llm_embedding_model。
    """
    res = await db.execute(select(ModelConfig).where(ModelConfig.level == "embedding"))  # noqa: E712
    cfg = res.scalars().first()
    if cfg and cfg.model:
        return cfg.model

    res = await db.execute(select(ModelConfig).where(ModelConfig.is_default == True))  # noqa: E712
    cfg = res.scalars().first()
    if cfg and cfg.embedding_model:
        return cfg.embedding_model

    return settings.llm_embedding_model
