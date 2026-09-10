"""提示词模板管理服务（设计文档 3.13）：列表/编辑/版本/恢复默认。"""
from __future__ import annotations

import json

from app.core.constants import PROMPTS_JSON
from app.db.repositories.prompt_repo import PromptTemplateRepository, PromptVersionRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork


class PromptService:
    def __init__(self, db: Database):
        self.db = db

    def list_templates(self) -> list:
        with self.db.session_ctx() as session:
            from sqlalchemy import select
            from app.db.models import PromptTemplate
            return list(session.scalars(select(PromptTemplate).order_by(PromptTemplate.key)))

    def get(self, key: str):
        with self.db.session_ctx() as session:
            return PromptTemplateRepository(session).get(key)

    def update_template(self, key: str, template: str) -> None:
        """保存新版本并记录历史快照。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = PromptTemplateRepository(session)
                tpl = repo.get(key)
                if tpl is None:
                    raise KeyError(key)
                new_version = tpl.version + 1
                PromptVersionRepository(session).add_version(key, tpl.version, tpl.template)
                repo.update(tpl, template=template, version=new_version)

    def restore_default(self, key: str) -> str | None:
        """从内置 prompts.json 恢复默认模板；返回恢复后的文本。"""
        if not PROMPTS_JSON.exists():
            return None
        with PROMPTS_JSON.open("r", encoding="utf-8") as f:
            templates = json.load(f)
        item = next((v for v in templates.values() if v["key"] == key), None)
        if item is None:
            return None
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                repo = PromptTemplateRepository(session)
                tpl = repo.get(key)
                if tpl is None:
                    repo.upsert(key=key, template=item["template"],
                                scope=item.get("scope", "global"),
                                category=item.get("category"), name=item.get("name"))
                else:
                    PromptVersionRepository(session).add_version(key, tpl.version, tpl.template)
                    repo.update(tpl, template=item["template"], version=tpl.version + 1)
        return item["template"]
