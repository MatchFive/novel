"""FastAPI 入口：SPA 静态托管 + 路由注册 + 生命周期。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.errors import register_exception_handlers
from app.database import AsyncSessionLocal, create_all, dispose_engine
from app.logging_config import setup_logging
from app.services.settings_seed import seed_default_models

logger = logging.getLogger(__name__)


def _register_routers(app: FastAPI) -> None:
    # 延迟导入，避免循环依赖；S1+ 逐步填充
    from app.api import projects, settings as settings_api, short_story, hotspots
    from app.api import long_outline, long_character, long_foreshadow, long_world, long_plot, long_chapter
    from app.api import assistant, long_continue, export, graph, log as log_api
    app.include_router(projects.router, prefix="/api/projects")
    app.include_router(settings_api.router, prefix="/api/settings")
    app.include_router(short_story.router, prefix="/api/short")
    app.include_router(hotspots.router, prefix="/api")
    app.include_router(long_outline.router, prefix="/api/long")
    app.include_router(long_character.router, prefix="/api/long")
    app.include_router(long_foreshadow.router, prefix="/api/long")
    app.include_router(long_world.router, prefix="/api/long")
    app.include_router(long_plot.router, prefix="/api/long")
    app.include_router(long_chapter.router, prefix="/api/long")
    app.include_router(assistant.router, prefix="/api/assistant")
    app.include_router(long_continue.router, prefix="/api/long")
    app.include_router(export.router, prefix="/api/export")
    app.include_router(graph.router, prefix="/api/graph")
    app.include_router(log_api.router, prefix="/api/log")


def _mount_spa(app: FastAPI) -> None:
    dist = settings.frontend_dist
    index = os.path.join(dist, "index.html")
    if os.path.isdir(dist) and os.path.isfile(index):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def _spa(full_path: str):
            # API 路径不接管
            if full_path.startswith("api"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not Found")
            return FileResponse(index)


def _run_migrations() -> None:
    """启动时运行幂等 schema 迁移（对旧库补列）；失败不阻断启动。"""
    try:
        from scripts.migrate import migrate
        migrate()
    except Exception:
        logger.exception("Schema migration failed at startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    _run_migrations()
    async with AsyncSessionLocal() as db:
        await seed_default_models(db)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    setup_logging(settings.log_level, Path(settings.log_dir))
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    @app.get("/health")
    async def _health():
        return {"ok": True, "status": "healthy", "app": settings.app_name}

    _register_routers(app)
    _mount_spa(app)
    return app


app = create_app()
