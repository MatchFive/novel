"""日志收集与导出接口：前端埋点、启动器日志、后端日志统一落到 data/logs。"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
frontend_logger = logging.getLogger("frontend")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEntry(BaseModel):
    level: LogLevel = "INFO"
    message: str = Field(..., min_length=1)
    source: str = "frontend"
    meta: dict | None = None


@router.post("")
async def collect_log(entry: LogEntry) -> dict:
    """前端或外部组件通过此接口写入日志。"""
    meta_str = f" | meta={entry.meta}" if entry.meta else ""
    record_msg = f"[{entry.source}] {entry.message}{meta_str}"
    level_method = {
        "DEBUG": frontend_logger.debug,
        "INFO": frontend_logger.info,
        "WARNING": frontend_logger.warning,
        "ERROR": frontend_logger.error,
        "CRITICAL": frontend_logger.critical,
    }.get(entry.level, frontend_logger.info)
    level_method(record_msg)
    return {"ok": True}


@router.get("/export")
async def export_logs() -> StreamingResponse:
    """打包 data/logs 下所有 .log 文件为 zip 下载。"""
    log_dir = Path(settings.log_dir)
    if not log_dir.exists():
        raise HTTPException(status_code=404, detail="日志目录不存在")

    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        raise HTTPException(status_code=404, detail="暂无日志文件")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in log_files:
            arc_name = file_path.name
            try:
                zf.write(file_path, arc_name)
            except Exception as exc:
                logger.warning("打包日志文件失败 %s: %s", file_path, exc)

    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"novel-studio-logs-{timestamp}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
