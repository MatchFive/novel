from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.llm_client import LLMClient
from app.database import get_db
from app.models import ModelConfig, UserSetting
from app.schemas.setting import (
    ModelConfigCreate,
    ModelConfigUpdate,
    ModelConfigTest,
    UserSettingUpdate,
)

router = APIRouter(tags=["settings"])


# ---------- 用户设置 ----------
async def _get_or_create_settings(db: AsyncSession) -> UserSetting:
    res = await db.execute(select(UserSetting))
    s = res.scalars().first()
    if not s:
        s = UserSetting()
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


@router.get("")
async def get_user_settings(db: AsyncSession = Depends(get_db)):
    s = await _get_or_create_settings(db)
    return s.to_dict()


@router.put("")
async def update_user_settings(payload: UserSettingUpdate, db: AsyncSession = Depends(get_db)):
    s = await _get_or_create_settings(db)
    if payload.recursive_limit is not None:
        if payload.recursive_limit < 1:
            raise ValidationError("递归上限必须 >= 1")
        s.recursive_limit = min(payload.recursive_limit, app_settings.recursive_limit_hard_cap)
    if payload.hotspot_sources is not None:
        s.hotspot_sources = payload.hotspot_sources
    if payload.theme is not None:
        s.theme = payload.theme
    await db.commit()
    await db.refresh(s)
    return s.to_dict()


# ---------- 模型配置 ----------
@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ModelConfig))
    return [m.to_dict() for m in res.scalars().all()]


@router.post("/models")
async def create_model(payload: ModelConfigCreate, db: AsyncSession = Depends(get_db)):
    if payload.is_default:
        res = await db.execute(select(ModelConfig).where(ModelConfig.is_default == True))  # noqa: E712
        for m in res.scalars().all():
            m.is_default = False
    m = ModelConfig(**payload.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m.to_dict()


@router.put("/models/{model_id}")
async def update_model(model_id: str, payload: ModelConfigUpdate, db: AsyncSession = Depends(get_db)):
    m = await db.get(ModelConfig, model_id)
    if not m:
        raise NotFoundError("模型配置不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_default"):
        res = await db.execute(select(ModelConfig).where(ModelConfig.is_default == True))  # noqa: E712
        for other in res.scalars().all():
            if other.id != model_id:
                other.is_default = False
    for k, v in data.items():
        setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return m.to_dict()


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, db: AsyncSession = Depends(get_db)):
    m = await db.get(ModelConfig, model_id)
    if not m:
        raise NotFoundError("模型配置不存在")
    await db.delete(m)
    await db.commit()
    return {"ok": True}


@router.post("/models/test")
async def test_model(payload: ModelConfigTest):
    client = LLMClient(base_url=payload.base_url, api_key=payload.api_key, model=payload.model)
    try:
        resp = await client.chat(
            [{"role": "user", "content": "ping"}],
            timeout=15,
        )
        return {"ok": True, "reply": (resp or "")[:200]}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
