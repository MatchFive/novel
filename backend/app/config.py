"""Pydantic Settings — 读取本地 .env / 配置文件。"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 前端构建产物目录（SPA 静态托管）
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR.parent / ".env"),
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

    # ---- Neo4j（可选镜像）----
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    neo4j_enabled: bool = False

    # ---- 热搜缓存 TTL（秒）----
    hotspot_cache_ttl: int = 3600

    @property
    def database_url(self) -> str:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
