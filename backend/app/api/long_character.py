from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.database import get_db
from app.repositories import (
    list_characters, create_character, update_character, delete_character,
)
from app.schemas.long import CharacterCreate, CharacterUpdate

router = APIRouter(prefix="/characters", tags=["long-character"])


@router.get("/{project_id}")
async def get_characters(project_id: str, db: AsyncSession = Depends(get_db)):
    return await list_characters(db, project_id)


@router.post("")
async def add_character(payload: CharacterCreate, db: AsyncSession = Depends(get_db)):
    return await create_character(db, payload.model_dump())


@router.put("/{character_id}")
async def edit_character(character_id: str, payload: CharacterUpdate, db: AsyncSession = Depends(get_db)):
    res = await update_character(db, character_id, payload.model_dump(exclude_unset=True))
    if not res:
        raise NotFoundError("角色不存在")
    return res


@router.delete("/{character_id}")
async def remove_character(character_id: str, db: AsyncSession = Depends(get_db)):
    ok = await delete_character(db, character_id)
    if not ok:
        raise NotFoundError("角色不存在")
    return {"ok": True}
