"""对话会话仓储：会话与消息的持久化（设计文档 4.4）。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import ChatMessage, ChatSession
from app.db.repositories.base import BaseRepository


class ChatSessionRepository(BaseRepository[ChatSession]):
    model = ChatSession

    def list_by_project(self, project_id: int) -> list[ChatSession]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.project_id == project_id)
            .order_by(ChatSession.updated_at.desc())
        )
        return list(self.session.scalars(stmt))

    def create_session(self, project_id: int, title: str = "新对话") -> ChatSession:
        s = ChatSession(project_id=project_id, title=title)
        self.session.add(s)
        return s

    def rename(self, session_id: int, title: str) -> ChatSession | None:
        """重命名会话（自动标题/手动改名）。"""
        s = self.session.get(ChatSession, session_id)
        if s is not None:
            s.title = title
        return s


class ChatMessageRepository(BaseRepository[ChatMessage]):
    model = ChatMessage

    def list_by_session(self, session_id: int, limit: int = 200) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def add_message(self, session_id: int, role: str, content: str) -> ChatMessage:
        m = ChatMessage(session_id=session_id, role=role, content=content)
        self.session.add(m)
        return m

    def first_user_message(self, session_id: int) -> ChatMessage | None:
        """会话的首条用户消息（自动补题用）。"""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.id)
            .limit(1)
        )
        return self.session.scalars(stmt).first()
