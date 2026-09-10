"""教程知识库服务（设计文档 3.13 / 6.4）：导入 → 切片 → 检索注入。"""
from __future__ import annotations

import re

from app.db.models import HandbookDoc
from app.db.repositories.canon_repo import HandbookChunkRepository, HandbookDocRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork

CHUNK_TARGET = 600  # 每切片目标字数

# 教程分类体系（文档级与切片级共用）
CATEGORIES = ["大纲", "正文", "角色", "世界观", "节奏", "市场", "其他"]

# 小标题识别：markdown 标题 / 【标题】 / 第一章|第一节 / 一、或 1. 等序号开头
_HEADING_RE = re.compile(
    r"^(#{1,6}\s*\S|【[^】]{1,30}】\s*$|【[^】]{1,30}】|第[0-9一二三四五六七八九十百]+[章节部分篇回]"
    r"|[0-9]{1,2}[、．.]\s*\S|[一二三四五六七八九十]{1,3}[、．]\s*\S)"
)
_SENT_END = "。，；：！？、…—,.;:!?）)」』"


class HandbookService:
    def __init__(self, db: Database):
        self.db = db

    # ---------------- 导入 ----------------
    def import_text(self, title: str, category: str, content: str,
                    project_id: int | None = None) -> HandbookDoc:
        """导入教程：切片并按序入库（原始切片，标题感知）。"""
        chunks = self._split(content)
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                doc = HandbookDocRepository(session).create(
                    project_id=project_id, title=title, category=category,
                    source_type="paste", content=content,
                )
                session.flush()
                repo = HandbookChunkRepository(session)
                for i, (ctitle, ctext) in enumerate(chunks):
                    repo.create(doc_id=doc.id, seq=i, title=ctitle, content=ctext)
                return doc

    async def import_with_extract(self, title: str, category: str, content: str,
                                  project_id: int | None, llm) -> HandbookDoc:
        """导入教程并用 LLM 提炼为写作要点（带逐条分类与检索标题，注入更精准，推荐）。"""
        points = await self._extract_points(llm, project_id, content)
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                doc = HandbookDocRepository(session).create(
                    project_id=project_id, title=title + "（AI 提炼）", category=category,
                    source_type="paste", content=content,
                )
                session.flush()
                self._replace_chunks(session, doc.id, points, content)
                return doc

    async def rechunk_doc(self, doc_id: int, llm=None, extract: bool = True) -> None:
        """重新切片已有教程（不改动原文）：AI 提炼重建，或按新切片策略重切原文。

        用于已导入教程的切片质量升级。
        """
        points: list[tuple[str, str, str]] = []
        with self.db.session_ctx() as session:
            doc = HandbookDocRepository(session).get(doc_id)
            if doc is None:
                raise ValueError(f"教程不存在: {doc_id}")
            content = doc.content or ""
            project_id = doc.project_id
        if extract and llm is not None:
            points = await self._extract_points(llm, project_id, content)
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                self._replace_chunks(session, doc_id, points, content)

    # ---------------- 提炼与切片重建 ----------------
    async def _extract_points(self, llm, project_id: int | None,
                              content: str) -> list[tuple[str, str, str]]:
        """调用 LLM 提炼要点，统一为 (标题, 分类, 内容)；失败返回空列表（调用方回退原文切片）。"""
        data = await llm.generate_structured(
            project_id=project_id, module="handbook", prompt_key="task.handbook_extract",
            system_prompt=llm.load_prompt("global.base_prompt", project_id),
            user_prompt=llm.load_prompt("task.handbook_extract", project_id=project_id,
                                        content=content[-12000:]),
        )
        return self._normalize_points((data or {}).get("points"))

    @staticmethod
    def _normalize_points(points) -> list[tuple[str, str, str]]:
        """统一 LLM 提炼结果为 (标题, 分类, 内容)；兼容旧版纯字符串要点。"""
        out: list[tuple[str, str, str]] = []
        for pt in points or []:
            if isinstance(pt, dict):
                content = str(pt.get("content") or "").strip()
                if not content:
                    continue
                title = str(pt.get("title") or "").strip()[:40] or content[:40]
                cat = str(pt.get("category") or "").strip()
                out.append((title, cat if cat in CATEGORIES else "", content))
            else:
                pt = str(pt).strip()
                if pt:
                    out.append((pt[:40], "", pt))
        return out

    def _replace_chunks(self, session, doc_id: int,
                        points: list[tuple[str, str, str]], content: str) -> None:
        """删除旧切片并写入新切片（提炼要点优先，空则回退原文标题感知切片）。"""
        repo = HandbookChunkRepository(session)
        for chunk in repo.list_by_doc(doc_id):
            repo.delete(chunk)
        session.flush()
        if points:
            for i, (ptitle, pcat, ptext) in enumerate(points):
                repo.create(doc_id=doc_id, seq=i, title=ptitle, content=ptext,
                            category=pcat or None)
        else:
            for i, (ctitle, ctext) in enumerate(self._split(content)):
                repo.create(doc_id=doc_id, seq=i, title=ctitle, content=ctext)

    def list_docs(self, project_id: int | None = None) -> list:
        with self.db.session_ctx() as session:
            docs = HandbookDocRepository(session).list_global_and_project(project_id)
        # 本项目教程排前，公用库在后（各自按导入顺序）
        docs.sort(key=lambda d: (0 if d.project_id == project_id else 1, d.id))
        return docs

    def get_doc(self, doc_id: int):
        with self.db.session_ctx() as session:
            return HandbookDocRepository(session).get(doc_id)

    def list_chunks(self, doc_id: int) -> list:
        with self.db.session_ctx() as session:
            return HandbookChunkRepository(session).list_by_doc(doc_id)

    def delete_doc(self, doc_id: int) -> None:
        """删除教程：先删其切片（handbook_chunks），再删文档（避免外键约束失败）。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                # 先删切片
                for chunk in HandbookChunkRepository(session).list_by_doc(doc_id):
                    HandbookChunkRepository(session).delete(chunk)
                # 再删文档
                doc = HandbookDocRepository(session).get(doc_id)
                if doc is not None:
                    HandbookDocRepository(session).delete(doc)

    def set_enabled(self, doc_id: int, enabled: bool) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                doc = HandbookDocRepository(session).get(doc_id)
                if doc is not None:
                    HandbookDocRepository(session).update(doc, enabled=enabled)

    # ---------------- 公用库 / 本项目 ----------------
    def set_scope(self, doc_id: int, project_id: int | None) -> None:
        """移动教程归属：project_id=None 设为公用（所有项目可用），否则归属单个项目。"""
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                doc = HandbookDocRepository(session).get(doc_id)
                if doc is not None:
                    HandbookDocRepository(session).update(doc, project_id=project_id)

    def copy_to_project(self, doc_id: int, project_id: int) -> HandbookDoc:
        """把公用教程激活（复制）到单个项目：含全部切片，副本可独立启停/删除。

        幂等：本项目已有同标题副本时直接返回既有副本，不重复复制。
        """
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                src = HandbookDocRepository(session).get(doc_id)
                if src is None:
                    raise ValueError(f"教程不存在: {doc_id}")
                for d in HandbookDocRepository(session).list_global_and_project(project_id):
                    if d.project_id == project_id and d.title == src.title:
                        return d
                doc = HandbookDocRepository(session).create(
                    project_id=project_id, title=src.title, category=src.category,
                    source_type=src.source_type, content=src.content,
                )
                session.flush()
                repo = HandbookChunkRepository(session)
                for c in HandbookChunkRepository(session).list_by_doc(doc_id):
                    repo.create(doc_id=doc.id, seq=c.seq, title=c.title,
                                content=c.content, category=c.category)
                return doc

    def search_chunks(self, keyword: str, category: str | None = None,
                      limit: int = 4, project_id: int | None = None) -> list:
        """检索注入：只查启用的教程（全局 + 当前项目），本项目教程优先。

        分类过滤按「切片级分类优先、空则沿用文档分类」匹配。
        """
        with self.db.session_ctx() as session:
            enabled_docs = HandbookDocRepository(session).list_enabled(project_id)
            enabled_ids = {d.id for d in enabled_docs}
            doc_cat = {d.id: (d.category or "") for d in enabled_docs}
            proj_doc_ids = {d.id for d in enabled_docs if d.project_id == project_id}
            chunks = HandbookChunkRepository(session).search(
                keyword, None, limit * 2, enabled_doc_ids=enabled_ids
            )
            if category:
                chunks = [
                    c for c in chunks
                    if (c.category or doc_cat.get(c.doc_id, "")) == category
                ]
            # 本项目教程切片优先排前
            chunks.sort(key=lambda c: 0 if c.doc_id in proj_doc_ids else 1)
            # 去重：公用教程激活到本项目后会产生相同切片，去重避免挤占注入名额（本项目优先）
            seen: set[tuple[str, str]] = set()
            uniq: list = []
            for c in chunks:
                key = ((c.title or ""), (c.content or ""))
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(c)
            return uniq[:limit]

    # ---------------- 切片 ----------------
    @staticmethod
    def _is_heading(line: str) -> bool:
        """判断一行是否为小标题（教程/网页复制的模块标题）。"""
        s = line.strip()
        if not s or len(s) > 40:
            return False
        if _HEADING_RE.match(s):
            return True
        # 短行且不以句读结尾（网页小标题常见形态，如「黄金三章」「钩子怎么写」）
        return len(s) <= 20 and s[-1] not in _SENT_END

    @classmethod
    def _split(cls, content: str) -> list[tuple[str, str]]:
        """标题感知切块：先按小标题分节（保留教程原有模块结构），再把相邻小节
        合并至目标字数；超长节按段落二次切分。段落首行作为切片标题兜底。"""
        # 1) 按标题分节
        sections: list[tuple[str, list[str]]] = []  # (heading, body_lines)
        cur_title = ""
        buf: list[str] = []
        for ln in content.splitlines():
            if cls._is_heading(ln):
                if cur_title or any(x.strip() for x in buf):
                    sections.append((cur_title, buf))
                cur_title = ln.strip().lstrip("#").strip().strip("【】")
                buf = []
            else:
                buf.append(ln)
        sections.append((cur_title, buf))

        # 2) 把相邻小节合并至目标字数；超长节按段落硬切
        blocks: list[tuple[str, str]] = []
        pack_title = ""
        pack: list[str] = []
        pack_len = 0

        def flush() -> None:
            nonlocal pack_title, pack, pack_len
            text = "\n".join(pack).strip()
            if text:
                blocks.append((pack_title or text.split("\n")[0][:40], text))
            pack_title, pack, pack_len = "", [], 0

        for title, body_lines in sections:
            body = "\n".join(body_lines).strip()
            sec_text = (f"{title}\n{body}" if title else body).strip()
            if not sec_text:
                continue
            if len(sec_text) > CHUNK_TARGET * 1.5:
                flush()
                blocks.extend(cls._split_paragraphs(sec_text, title))
                continue
            if pack and pack_len + len(sec_text) > CHUNK_TARGET:
                flush()
            if not pack:
                pack_title = title
            pack.append(sec_text)
            pack_len += len(sec_text)
        flush()
        return blocks

    @staticmethod
    def _split_paragraphs(text: str, title: str = "") -> list[tuple[str, str]]:
        """超长文本按段落硬切（空行分段，合并至目标字数）。"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        blocks: list[tuple[str, str]] = []
        cur_title = title
        cur: list[str] = []
        cur_len = 0
        for para in paragraphs:
            if cur and cur_len + len(para) > CHUNK_TARGET:
                blocks.append((cur_title, "\n\n".join(cur)))
                cur_title, cur, cur_len = "", [], 0
            if not cur and not cur_title:
                cur_title = para.split("\n")[0][:40]
            cur.append(para)
            cur_len += len(para)
        if cur:
            blocks.append((cur_title, "\n\n".join(cur)))
        return blocks
