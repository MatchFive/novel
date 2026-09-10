"""提示词模板 / 版本 / 应用设置仓储。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import AppSetting, PromptTemplate, PromptVersion
from app.db.repositories.base import BaseRepository


class PromptTemplateRepository(BaseRepository[PromptTemplate]):
    model = PromptTemplate

    def get_effective(self, key: str, project_id: int | None = None) -> PromptTemplate | None:
        """按覆盖优先级取模板：project scope > global scope。"""
        if project_id is not None:
            stmt = select(PromptTemplate).where(
                PromptTemplate.key == key,
                PromptTemplate.scope == "project",
                PromptTemplate.project_id == project_id,
            )
            t = self.session.scalar(stmt)
            if t:
                return t
        stmt = select(PromptTemplate).where(
            PromptTemplate.key == key, PromptTemplate.scope == "global"
        )
        return self.session.scalar(stmt)

    def upsert(self, key: str, template: str, *, scope: str = "global",
               project_id: int | None = None, category: str | None = None,
               name: str | None = None) -> PromptTemplate:
        t = self.session.get(PromptTemplate, key)
        if t is None:
            t = PromptTemplate(key=key, template=template, scope=scope,
                               project_id=project_id, category=category, name=name, version=1)
            self.session.add(t)
        else:
            t.template = template
            t.scope = scope
            t.version += 1
        return t


class PromptVersionRepository(BaseRepository[PromptVersion]):
    model = PromptVersion

    def add_version(self, key: str, version: int, template: str) -> PromptVersion:
        v = PromptVersion(key=key, version=version, template=template)
        self.session.add(v)
        return v


class SettingRepository(BaseRepository[AppSetting]):
    model = AppSetting

    def get_value(self, key: str, default: str | None = None) -> str | None:
        row = self.session.get(AppSetting, key)
        return row.value if row else default

    def set_value(self, key: str, value: str) -> None:
        row = self.session.get(AppSetting, key)
        if row is None:
            self.session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
