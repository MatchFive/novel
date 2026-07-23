"""Pydantic Settings — 读取本地 .env / 配置文件。
打包后可执行文件运行时，数据目录放在可执行文件同级，静态资源从 PyInstaller bundle 读取。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    # PyInstaller one-file/one-dir: 资源在 _MEIPASS，可执行文件在 exe 目录
    _BUNDLE_DIR = Path(sys._MEIPASS)
    _EXE_DIR = Path(sys.executable).resolve().parent
    BASE_DIR = _BUNDLE_DIR
    DATA_DIR = _EXE_DIR / "data"
    FRONTEND_DIST = _BUNDLE_DIR / "frontend" / "dist"
    ENV_FILE = _EXE_DIR / ".env"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR.parent / "data"
    FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
    ENV_FILE = BASE_DIR.parent / ".env"

DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "Novel Studio"
    debug: bool = True

    # ---- 数据库 ----
    db_path: str = str(DATA_DIR / "novel.db")

    # ---- 前端 ----
    frontend_dist: str = str(FRONTEND_DIST)

    # ---- Agent 递归取数 ----
    recursive_limit_default: int = 8
    recursive_limit_hard_cap: int = 30

    # ---- LLM 默认（可被 model_configs 表覆盖）----
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.7
    llm_timeout: float = 120.0
    llm_embedding_model: str = "text-embedding-3-small"
    llm_embedding_dimension: int = 1536

    # ---- Neo4j（可选镜像）----
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    neo4j_enabled: bool = False

    # ---- 日志 ----
    log_level: str = "INFO"
    log_dir: str = str(DATA_DIR / "logs")

    @property
    def database_url(self) -> str:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
