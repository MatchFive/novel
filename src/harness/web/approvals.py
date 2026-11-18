"""审批 API（Task 11）：GET pending 现值 + POST answer。

与 repl ``/answer`` **同一写盘函数**（:func:`harness.run.approval.write_answer`）——
web 与 repl 共用 ``.harness/pending.md`` + ``pending.answer`` 文件协议，校验口径一致：

- 校验失败（越界/单选多给/空选）→ 400 ``{"error": ...}``（**绝不写** pending.answer）；
- pending 已被答/不存在（:class:`PendingMissingError`）→ 409；
- 合法 → 200 ``{"ok": true}``，写盘格式与 repl 逐字一致。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from harness.run.approval import (
    _CANCEL_MARKERS,
    InvalidAnswerError,
    PendingMissingError,
    cancel_answer,
    parse_pending_md,
    write_answer,
)
from harness.store.project import ProjectStore


class AnswerBody(BaseModel):
    """POST approvals/answer：选项号列表（1 起）+ 可选备注；空列表 + 取消备注 = 取消。"""

    indices: list[int] = []
    note: str = ""


def _pending_path(store: ProjectStore) -> Path:
    return store.root / ".harness" / "pending.md"


def register(router: APIRouter, store_dep) -> None:
    """把 /approvals/pending + /approvals/answer 挂到 /api 路由上（app.py 装配时调用）。"""

    @router.get("/project/{project_id}/approvals/pending")
    def get_pending(store: ProjectStore = store_dep) -> dict:
        pending = _pending_path(store)
        parsed = None
        if pending.exists():
            try:
                parsed = parse_pending_md(pending.read_text(encoding="utf-8"))
            except OSError:
                parsed = None
        if parsed is None:
            return {"pending": False}
        return {"pending": True, **parsed}

    @router.post("/project/{project_id}/approvals/answer")
    def post_answer(body: AnswerBody, store: ProjectStore = store_dep) -> dict:
        base = store.root / ".harness"
        try:
            if body.indices:
                write_answer(base, indices=body.indices, note=body.note or None)
            elif body.note.strip().casefold() in _CANCEL_MARKERS:
                if not _pending_path(store).exists():
                    raise PendingMissingError(f"没有待审批的问题：{_pending_path(store)} 不存在")
                cancel_answer(base)
            else:
                raise InvalidAnswerError("至少选择一个选项号")
        except InvalidAnswerError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except PendingMissingError as exc:
            return JSONResponse(status_code=409, content={"error": str(exc)})
        return {"ok": True}
