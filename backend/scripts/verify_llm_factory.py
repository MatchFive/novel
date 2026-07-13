"""验证 llm_factory 的选择逻辑。"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.config import settings
from app.core.llm_factory import get_llm_client, get_embedding_model_name, get_default_llm_client
from app.api.settings import _ensure_level_unique
from app.database import create_all, engine
from app.models import ModelConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def main():
    # 使用临时数据库，避免污染 data/novel.db
    with tempfile.TemporaryDirectory() as tmpdir:
        settings.db_path = str(Path(tmpdir) / "test.db")
        # 重新创建 engine 以使用新 db_path（settings.database_url 是 property，但 engine 已按旧 url 创建）
        # 这里通过直接修改 engine.url 并不安全，故通过重建 engine 实现
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.database import Base

        test_engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
        )
        TestSession = async_sessionmaker(
            bind=test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestSession() as db:
            # 1. 无配置时：回退到 .env 默认
            client = await get_llm_client(db, "high")
            assert client.model == settings.llm_model, f"expected fallback model, got {client.model}"
            print(f"[PASS] fallback model: {client.model}")

            emb = await get_embedding_model_name(db)
            assert emb == settings.llm_embedding_model, f"expected fallback embedding, got {emb}"
            print(f"[PASS] fallback embedding: {emb}")

            # 2. 添加全局默认配置
            default_cfg = ModelConfig(
                name="default",
                base_url="https://default.example.com/v1",
                api_key="default-key",
                model="global-default-model",
                embedding_model="global-default-embedding",
                is_default=True,
            )
            db.add(default_cfg)
            await db.commit()

            client = await get_llm_client(db, "low")
            assert client.model == "global-default-model", f"expected global default, got {client.model}"
            print(f"[PASS] level without config uses global default: {client.model}")

            emb = await get_embedding_model_name(db)
            assert emb == "global-default-embedding", f"expected default embedding, got {emb}"
            print(f"[PASS] default config embedding: {emb}")

            # 3. 添加 level=high 配置
            high_cfg = ModelConfig(
                name="high",
                base_url="https://high.example.com/v1",
                api_key="high-key",
                model="high-model",
                level="high",
            )
            db.add(high_cfg)
            await db.commit()

            client = await get_llm_client(db, "high")
            assert client.model == "high-model", f"expected high model, got {client.model}"
            print(f"[PASS] level=high selects high model: {client.model}")

            # 4. 添加 level=medium 配置
            medium_cfg = ModelConfig(
                name="medium",
                base_url="https://medium.example.com/v1",
                api_key="medium-key",
                model="medium-model",
                level="medium",
            )
            db.add(medium_cfg)
            await db.commit()

            client = await get_llm_client(db, "medium")
            assert client.model == "medium-model", f"expected medium model, got {client.model}"
            print(f"[PASS] level=medium selects medium model: {client.model}")

            # 5. level=low 没有配置，回退到全局默认
            client = await get_llm_client(db, "low")
            assert client.model == "global-default-model", f"expected global default for low, got {client.model}"
            print(f"[PASS] level=low falls back to global default: {client.model}")

            # 6. 添加 level=embedding 配置
            emb_cfg = ModelConfig(
                name="embedding",
                base_url="https://emb.example.com/v1",
                api_key="emb-key",
                model="emb-special-model",
                level="embedding",
            )
            db.add(emb_cfg)
            await db.commit()

            emb = await get_embedding_model_name(db)
            assert emb == "emb-special-model", f"expected embedding config model, got {emb}"
            print(f"[PASS] level=embedding embedding model: {emb}")

            # 7. 兼容别名
            client = await get_default_llm_client(db)
            assert client.model == "global-default-model", f"expected alias to return default, got {client.model}"
            print(f"[PASS] get_default_llm_client alias works: {client.model}")

            # 8. 测试 settings level 唯一性：再次创建 level=high 时，旧 high 配置 level 被置空
            new_high = ModelConfig(
                name="new-high",
                base_url="https://new-high.example.com/v1",
                api_key="new-high-key",
                model="new-high-model",
                level="high",
            )
            db.add(new_high)
            await db.commit()
            await db.refresh(new_high)
            await _ensure_level_unique(db, new_high.level, exclude_id=new_high.id)
            await db.commit()

            old = await db.get(ModelConfig, high_cfg.id)
            assert old.level is None, f"expected old high level cleared, got {old.level}"
            print(f"[PASS] level uniqueness: old high config level cleared")

            print("\nAll tests passed.")

        await test_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
