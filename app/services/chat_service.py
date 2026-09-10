"""AI 创作对话服务：连续对话 + 携带项目上下文（设定/角色）+ 流式回复。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.chat_repo import ChatMessageRepository, ChatSessionRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork
from app.services.llm_service import LLMService
from app.services.read_tools import extract_read_calls, run_read_tool

MAX_HISTORY = 8  # 携带的历史轮数（1 条=一问一答）

DEFAULT_SESSION_TITLE = "新对话"
TITLE_MAX_LEN = 18  # 会话标题最大字数


def derive_title(text: str, max_len: int = TITLE_MAX_LEN) -> str:
    """从消息文本派生会话标题：取首行、压缩空白、截断。"""
    if not text or not text.strip():
        return DEFAULT_SESSION_TITLE
    line = " ".join(text.strip().splitlines()[0].split())
    if not line:
        return DEFAULT_SESSION_TITLE
    return line[:max_len] + ("…" if len(line) > max_len else "")

# 修改/增删意图动词（触发"计划确认式执行"，而非普通对话）
MODIFY_INTENT_VERBS = (
    "改成", "改为", "修改", "调整", "增加", "添加", "新增", "新建", "创建",
    "删除", "去掉", "移除", "加上", "把", "设定", "角色", "章节", "大纲",
    "更新", "写入", "入库", "录入", "存到", "保存到", "生成", "细纲",
)


def is_modification_intent(message: str) -> bool:
    """判断消息是否为"修改/增删项目实体"的意图（关键词兜底版）。"""
    return any(v in message for v in MODIFY_INTENT_VERBS)


class ChatService:
    def __init__(self, db: Database, llm_service: LLMService):
        self.db = db
        self.llm = llm_service

    # ---------------- 意图分类（LLM 优先，关键词兜底） ----------------
    async def classify_intent(self, project_id: int | None, message: str) -> bool:
        """判断消息是否为"修改/增删项目实体"的意图。

        优先走 task.intent_classify 路由（可在「模型与路由设置」中指定本地小模型，
        如 Ollama/LM Studio 的 OpenAI 兼容接口）；本地不可用/超时/输出模糊时
        自动回退到关键词匹配（保底不卡死对话）。
        """
        try:
            resolved = self.llm.resolve_plain(project_id, "chat", prompt_key="task.intent_classify")
            prompt = self.llm.load_prompt(
                "task.intent_classify", project_id=project_id, message=message[:300])
            buf = ""
            async for chunk in self.llm.stream_raw(
                [{"role": "user", "content": prompt}], resolved,
                project_id=project_id, module="chat", prompt_key="task.intent_classify",
            ):
                buf += chunk
            out = (buf or "").strip()
            has_mod = "修改" in out
            has_chat = "讨论" in out
            if has_mod and not has_chat:
                return True
            if has_chat and not has_mod:
                return False
        except Exception:  # 本地模型不可用/超时等 → 静默回退
            pass
        return is_modification_intent(message)

    # ---------------- 项目上下文 ----------------
    def build_project_context(self, project_id: int | None, keywords: str = "") -> str:
        """把项目信息 + 相关设定 + 角色列表组装为对话背景（空项目时返回通用提示）。

        相关性排序：配置了嵌入服务（[llm.embedding]）时按语义相似度挑选设定/角色，
        未配置或服务不可用时回退 FTS 关键词检索 + 前 N 条兜底。
        """
        if project_id is None:
            return "（尚未打开项目——可讨论通用创作话题）"
        from app.services.embedding_service import EmbeddingService
        from app.services.read_tools import _decode_text
        emb = EmbeddingService(getattr(self.llm, "cfg", None))
        with self.db.session_ctx() as session:
            project = ProjectRepository(session).get(project_id)
            all_entries = WorldEntryRepository(session).list_by_category(project_id, None)[:30]
            all_chars = CharacterRepository(session).list_by_role(project_id)[:15]

        # 设定/角色挑选：语义检索优先，FTS/前 N 条兜底
        entries, chars = [], []
        if emb.enabled and keywords.strip() and all_entries:
            ranked = emb.rank_texts(
                keywords,
                [f"{_decode_text(e.title)}：{_decode_text(e.content)[:120]}" for e in all_entries],
                top_k=8)
            if ranked is not None:
                entries = [all_entries[i] for i in ranked]
        if emb.enabled and keywords.strip() and all_chars:
            ranked = emb.rank_texts(
                keywords,
                [f"{_decode_text(c.name)}（{c.role_type or ''}）："
                 f"{(c.profile or {}).get('personality', '')} {(c.profile or {}).get('background', '')[:80]}"
                 for c in all_chars],
                top_k=10)
            if ranked is not None:
                chars = [all_chars[i] for i in ranked]
        if not entries:
            if keywords.strip():
                with self.db.session_ctx() as session:
                    entries = WorldEntryRepository(session).search(
                        project_id, keywords.strip()[:20], limit=6)
            if not entries:
                entries = all_entries[:10]
        if not chars:
            chars = all_chars[:10]

        lines = [f"【作品】《{_decode_text(project.name)}》｜类型：{project.genre or '未分类'}"]
        if project.logline:
            lines.append(f"【一句话梗概】{_decode_text(project.logline)}")
        if project.theme:
            lines.append(f"【主题】{_decode_text(project.theme)}")
        if project.style_guide:
            lines.append(f"【文风指南】{_decode_text(project.style_guide)}")
        if entries:
            lines.append("【相关设定】")
            lines.extend(f"- {_decode_text(e.title)}：{_decode_text(e.content)[:80]}" for e in entries)
        if chars:
            lines.append("【角色】")
            lines.extend(f"- {_decode_text(c.name)}（{c.role_type or '?'}）" for c in chars)
        return "\n".join(lines)

    # ---------------- 对话 ----------------
    async def chat_stream(self, project_id: int | None, history: list[dict],
                          message: str) -> AsyncIterator[str]:
        """流式对话：system=基础提示词+项目上下文，history=最近 N 轮，最后为当前消息。"""
        base = self.llm.load_prompt("global.base_prompt", project_id)
        ctx = self.build_project_context(project_id, message)
        system = (
            f"{base}\n\n"
            f"【当前项目上下文】\n{ctx}\n\n"
            "你是作者的创作讨论伙伴：帮助梳理点子、补充世界观与角色设定、讨论剧情走向、"
            "提供写作建议。回答用简体中文；可反问澄清；当用户要求'记下来/存为点子'时，"
            "把讨论要点整理成 1~2 段可入库的总结。\n"
            "【重要】你在普通对话中【无法修改项目数据】（设定/角色/章节/细纲/正文都写不进数据库）。"
            "绝不要声称'已更新/已入库/已写入'——那是欺骗用户。"
            "当用户想把讨论内容落到项目里时，明确告诉TA：请用指令句式再说一遍"
            "（如『把前三章的细纲入库』『更新第1章正文为…』『新增设定…』），"
            "系统会生成修改计划、由用户确认后才会真正执行。"
        )
        from app.services.read_tools import TOOL_GUIDE_CHAT
        system += "\n\n" + TOOL_GUIDE_CHAT
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": message})

        resolved = self.llm.resolve_plain(project_id, "chat")
        # 工具循环：模型输出 [[READ:...]] 标记时执行只读查询、回灌结果后再答（最多 3 轮）
        for _ in range(3):
            full_response = ""
            async for chunk in self.llm.stream_raw(messages, resolved, project_id=project_id,
                                                   module="chat", prompt_key="chat"):
                full_response += chunk
                # 在工具调用模式下，不透传中间 chunk，等完整响应后再处理
            
            # 检查是否是纯工具调用
            from app.services.read_tools import extract_read_calls, is_read_only_output
            calls = extract_read_calls(full_response)
            
            if not calls or not is_read_only_output(full_response):
                # 不是工具调用，或者是混合内容，直接返回完整响应
                yield full_response
                return
            
            # 是纯工具调用，执行查询并回灌结果
            results = []
            for kind, arg in calls[:3]:
                results.append(f"【{kind} {arg}】\n{run_read_tool(self.db, project_id, kind, arg)}")
            
            # 将工具调用和结果加入对话历史，进入下一轮
            messages.append({"role": "assistant", "content": full_response})
            messages.append({"role": "user",
                             "content": "【读取结果】\n" + "\n\n".join(results)
                                        + "\n\n数据已给你，请正式回答用户的问题。"})
        # 超过最大轮次，返回最后一轮响应
        yield full_response

    # ---------------- 会话持久化（长期保存，类似 Claude Code 对话保存） ----------------
    def list_sessions(self, project_id: int) -> list:
        with self.db.session_ctx() as session:
            return ChatSessionRepository(session).list_by_project(project_id)

    def create_session(self, project_id: int, title: str = "新对话"):
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                s = ChatSessionRepository(session).create_session(project_id, title)
                return s

    def save_message(self, session_id: int, role: str, content: str) -> None:
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ChatMessageRepository(session).add_message(session_id, role, content)

    def load_history(self, session_id: int, limit: int = 200) -> list:
        with self.db.session_ctx() as session:
            return ChatMessageRepository(session).list_by_session(session_id, limit)

    # ---------------- 会话标题（聊完自动更新，便于分辨） ----------------
    def rename_session(self, session_id: int, title: str) -> None:
        title = (title or "").strip()[:40]
        if not title:
            return
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                ChatSessionRepository(session).rename(session_id, title)

    def maybe_set_default_title(self, session_id: int, first_message: str) -> str | None:
        """会话仍是默认标题「新对话」时，用首条消息派生标题；返回新标题或 None。"""
        with self.db.session_ctx() as session:
            repo = ChatSessionRepository(session)
            s = repo.get(session_id)
            if s is None or (s.title or "").strip() != DEFAULT_SESSION_TITLE:
                return None
            title = derive_title(first_message)
            if title == DEFAULT_SESSION_TITLE:
                return None
            with UnitOfWork(session) as uow:
                repo.rename(session_id, title)
            return title

    def backfill_default_titles(self, project_id: int) -> None:
        """打开项目时修补历史会话：仍叫「新对话」且已有消息的，用首条消息补题。"""
        with self.db.session_ctx() as session:
            repo = ChatSessionRepository(session)
            msgs = ChatMessageRepository(session)
            with UnitOfWork(session) as uow:
                for s in repo.list_by_project(project_id):
                    if (s.title or "").strip() != DEFAULT_SESSION_TITLE:
                        continue
                    m = msgs.first_user_message(s.id)
                    if m is None:
                        continue
                    title = derive_title(m.content or "")
                    if title != DEFAULT_SESSION_TITLE:
                        repo.rename(s.id, title)

    async def generate_title(self, project_id: int | None,
                             user_text: str, ai_text: str) -> str | None:
        """首轮对话后用 LLM 生成更贴切的会话标题；失败或为空返回 None（调用方静默忽略）。"""
        messages = [
            {"role": "system",
             "content": "你是会话标题生成器。根据给定对话，输出一个不超过 16 个字的简体中文标题，"
                        "概括这段对话讨论的主题。只输出标题本身：不加引号、不加书名号、不加结尾标点、不解释。"},
            {"role": "user",
             "content": f"【用户】{user_text[:300]}\n【助手】{ai_text[:500]}"},
        ]
        resolved = self.llm.resolve_plain(project_id, "chat")
        buf = ""
        async for chunk in self.llm.stream_raw(messages, resolved, project_id=project_id,
                                               module="chat", prompt_key="chat_title"):
            buf += chunk
        lines = [ln.strip() for ln in (buf or "").splitlines() if ln.strip()]
        if not lines:
            return None
        title = lines[0].strip("「」\"'《》*# 　").rstrip("。：:，,！!？?")
        if not title:
            return None
        return title[:TITLE_MAX_LEN] + ("…" if len(title) > TITLE_MAX_LEN else "")

    # ---------------- 档案解析入库（"记下来"出口） ----------------
    async def parse_character(self, project_id: int, text: str) -> dict:
        """把 AI 整理的角色档案文本解析为角色卡结构。"""
        data = await self.llm.generate_structured(
            project_id=project_id, module="character", prompt_key="task.character_parse",
            system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
            user_prompt=self.llm.load_prompt(
                "task.character_parse", project_id=project_id, text=text[-6000:],
            ),
        )
        return data if isinstance(data, dict) else {}

    async def parse_world_entry(self, project_id: int, text: str) -> dict:
        """把 AI 整理的设定文本解析为设定条目结构。"""
        data = await self.llm.generate_structured(
            project_id=project_id, module="world", prompt_key="task.world_parse",
            system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
            user_prompt=self.llm.load_prompt(
                "task.world_parse", project_id=project_id, text=text[-6000:],
            ),
        )
        return data if isinstance(data, dict) else {}
