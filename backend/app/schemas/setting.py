from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field


class ModelConfigCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    level: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    temperature: Optional[float] = None
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    level: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    temperature: Optional[float] = None
    is_default: Optional[bool] = None


class ModelConfigTest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    kind: Optional[str] = "chat"  # "chat" | "embedding"
    embedding_dimension: Optional[int] = None


class ModelConfigFetch(BaseModel):
    base_url: str
    api_key: str = ""


class UserSettingUpdate(BaseModel):
    recursive_limit: Optional[int] = None
    theme: Optional[str] = None
    assistant_summary_threshold: Optional[int] = None
    assistant_max_summaries: Optional[int] = None
    assistant_summary_max_length: Optional[int] = None
    assistant_history_recent_messages: Optional[int] = None
    assistant_history_top_k: Optional[int] = None
    content_rating: Optional[str] = None
    chapter_target_words: Optional[int] = None
