"""AI 调用留痕仓储。"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import GenerationLog
from app.db.repositories.base import BaseRepository


class GenerationLogRepository(BaseRepository[GenerationLog]):
    model = GenerationLog

    def add_log(self, *, project_id: int | None = None, module: str | None = None,
                caller: str | None = None, provider: str | None = None,
                tier: str | None = None, model: str | None = None,
                prompt_key: str | None = None, input_summary: str | None = None,
                output_summary: str | None = None, in_tokens: int = 0,
                out_tokens: int = 0, duration_ms: int = 0,
                status: str = "ok", error: str | None = None) -> GenerationLog:
        log = GenerationLog(
            project_id=project_id, module=module, caller=caller, provider=provider,
            tier=tier, model=model, prompt_key=prompt_key,
            input_summary=input_summary, output_summary=output_summary,
            in_tokens=in_tokens, out_tokens=out_tokens, duration_ms=duration_ms,
            status=status, error=error,
        )
        self.session.add(log)
        return log

    def list_by_project(self, project_id: int, status: str | None = None,
                        limit: int = 300) -> list[GenerationLog]:
        stmt = select(GenerationLog).where(GenerationLog.project_id == project_id)
        if status:
            stmt = stmt.where(GenerationLog.status == status)
        stmt = stmt.order_by(GenerationLog.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def totals(self, project_id: int | None = None) -> dict:
        """按 provider+tier 汇总 token 用量（成本面板用）。"""
        stmt = select(
            GenerationLog.provider, GenerationLog.tier,
            func.sum(GenerationLog.in_tokens), func.sum(GenerationLog.out_tokens),
            func.count(GenerationLog.id),
        )
        if project_id is not None:
            stmt = stmt.where(GenerationLog.project_id == project_id)
        stmt = stmt.group_by(GenerationLog.provider, GenerationLog.tier)
        rows = self.session.execute(stmt).all()
        return [
            {"provider": r[0], "tier": r[1], "in_tokens": r[2] or 0,
             "out_tokens": r[3] or 0, "calls": r[4] or 0}
            for r in rows
        ]
