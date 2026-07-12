"""根据用户默认模型配置构造 LLMClient。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_client import LLMClient
from app.models import ModelConfig


async def get_default_llm_client(db: AsyncSession) -> LLMClient:
    """优先使用用户设置的默认模型配置；没有则回退到 .env 默认。"""
    res = await db.execute(select(ModelConfig).where(ModelConfig.is_default == True))  # noqa: E712
    cfg = res.scalars().first()
    if cfg:
        return LLMClient(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or None,
            model=cfg.model or None,
        )
    return LLMClient()
