"""上下文组装器（设计文档 6.4）：分层 + 检索 + 预算裁剪。

组装器经 Repository 读取数据（遵循 4.4），产出 system/user prompt 与注入命中统计。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.db.models import Chapter
from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.chapter_repo import ArcRepository, ChapterRepository
from app.db.repositories.prompt_repo import PromptTemplateRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.llm.memory_injector import MemoryBundle, MemoryInjector
from app.llm.prompts import render

# 预算（token 近似值；中文按字符粗估）
BUDGET_TOKENS = 32_000
CHARS_PER_TOKEN = 0.8  # 中文 1 字 ≈ 0.8~1 token，英文更省；粗估用


@dataclass
class AssemblyResult:
    system_prompt: str
    user_prompt: str
    injected: dict = field(default_factory=dict)  # 各区块命中数量/截断标记
    used_chars: int = 0


class ContextAssembler:
    """按任务组装上下文；当前支持正文生成（draft_continue）。"""

    def __init__(self, db: Database):
        self.db = db
        self.memory = MemoryInjector(db)

    # ---------------- 模板 ----------------
    def load_prompt(self, key: str, project_id: int | None = None, **kwargs) -> str:
        with self.db.session_ctx() as session:
            tpl = PromptTemplateRepository(session).get_effective(key, project_id)
        if tpl is None:
            raise KeyError(f"提示词模板不存在: {key}")
        return render(tpl.template, **kwargs)

    # ---------------- 正文生成组装 ----------------
    def build_draft_prompt(
        self,
        *,
        project_id: int,
        chapter: Chapter | None,
        gen_config: dict,
        recent_draft: str,
        target_beat: str,
        recent_summaries: str = "",
        memory: MemoryBundle | None = None,
        extra_keywords: list[str] | None = None,
        web_facts: str = "",
    ) -> AssemblyResult:
        base = self.load_prompt("global.base_prompt", project_id)
        if gen_config.get("draft_mode") == "whole":
            # 整章单发：精简系统提示词，匹配「细纲→整章正文」SFT 模型的训练格式
            style = gen_config.get("style", "") or "网文流畅"
            tw = gen_config.get("target_words", 3000)
            system = (
                "你是一位网络小说作者，请严格按照给定的章节细纲扩写成完整章节正文。\n"
                f"文风：{style}；目标字数：约 {tw} 字；语言：简体中文。\n"
                "要求：按细纲节拍顺序推进，不遗漏节拍、不虚构细纲之外的组织/角色/主线事件；"
                "结尾落在细纲的结尾钩子上。输出连贯叙事文字，不分节、不加标题。"
            )
        else:
            system = base + "\n\n【生成配置】" + json.dumps(
                {k: v for k, v in gen_config.items() if v is not None}, ensure_ascii=False
            )

        chunks: list[tuple[str, str]] = []  # (区块名, 文本)，按丢弃优先级从高到低排列

        # 可丢弃区块（裁剪顺序：先丢手册 → 联网核查 → 旧正文 → 世界观 → 他人经历 → 事件 → 承诺）
        chunks.append(("handbook", self._fmt("【写作技巧参考】",
                                             self._retrieve_handbook(project_id, chapter,
                                                                     extra_keywords))))
        chunks.append(("web_facts", self._fmt("【现实知识核查（联网检索，仅供校准事实）】", web_facts)))
        chunks.append(("recent_draft", self._fmt("【最近正文】", recent_draft)))
        chunks.append(("world_entries", self._fmt("【世界观设定（检索命中）】",
                                                  self._retrieve_world(project_id, chapter,
                                                                       extra_keywords))))
        chunks.append(("other_char_events", self._fmt("【其他角色相关经历】",
                                                      memory.other_char_events if memory else "")))
        chunks.append(("related_events", self._fmt("【相关历史事件】",
                                                   memory.related_events if memory else "")))
        chunks.append(("open_promises", self._fmt("【未回收承诺/伏笔提醒】",
                                                  memory.open_promises if memory else "")))

        # 前情概览（章节摘要）
        chunks.append(("rolling_summary", self._fmt("【前情提要（概览）】", recent_summaries)))

        # 核心区块（不可丢，可压缩）
        core: list[tuple[str, str]] = [
            ("arc_outline", self._fmt("【当前卷大纲】", self._retrieve_arc(chapter))),
            ("chapter_objective", self._fmt("【本章目标】", chapter.objective if chapter else "")),
            ("remaining_beats", self._fmt("【本章细纲】",
                                          self._beats_text(chapter, target_beat))),
            ("cast_cards", self._fmt("【出场角色】", self._retrieve_cast(project_id, chapter))),
            ("pov_memories", self._fmt("【视点角色经历档案】",
                                       memory.pov_memories if memory else "")),
            ("knowledge_state", self._fmt("【知识状态（严格遵守）】",
                                          memory.knowledge_state if memory else "")),
        ]

        task_block = self.load_prompt(
            "task.draft_continue", project_id,
            style_guide=gen_config.get("style", "") or "",
            generation_profile="",
            arc_outline="", chapter_objective="", pov_character="", remaining_beats="",
            retrieved_entries="", cast_cards="", pov_memories="", knowledge_state="",
            related_events="", open_promises="", rolling_summary="", recent_draft="",
            target_beat=(f"续写「{target_beat}」对应的内容（只写这一拍，写完即停）。"
                         if target_beat else
                         f"把【本章细纲】完整扩写成本章正文（约 {gen_config.get('target_words', 3000)} 字），"
                         "按节拍顺序推进，结尾落在结尾钩子上。"),
        )
        # 去掉模板中的空占位区块标记，得到任务指令部分
        task_text = self._clean_placeholders(task_block)
        chunks.append(("task", self._fmt("【任务】", task_text)))

        # 预算裁剪：可丢弃区块按顺序累加，超出预算则截断
        used = len(system) + sum(len(t) for _, t in core)
        kept: list[tuple[str, str]] = []
        for name, text in chunks:
            if used + len(text) > int(BUDGET_TOKENS / CHARS_PER_TOKEN):
                if name == "recent_draft":  # 旧正文截断保留尾部
                    tail = text[-3000:]
                    kept.append((name, tail))
                    used += len(tail)
                    continue
                break
            kept.append((name, text))
            used += len(text)

        # 最终组装：核心区块（卷大纲/本章目标/本章细纲/角色卡/记忆/知识状态）必须在内——
        # 修复：之前 core 只参与预算计算、从未拼进 user prompt（细纲根本没注入，模型只能瞎写）
        core_texts = [t for _, t in core if t]
        user = "\n\n".join(core_texts + [t for _, t in kept if t])
        injected = dict(memory.hits if memory else {})
        injected["world_entries"] = self._count_retrieved(project_id, chapter)
        injected["kept_blocks"] = [n for n, _ in kept]
        return AssemblyResult(system_prompt=system, user_prompt=user,
                              injected=injected, used_chars=used)

    # ---------------- 检索 ----------------
    def catalog(self, project_id: int) -> str:
        """资源目录（紧凑版）：供「上下文策划」模型挑选要注入的内容。"""
        with self.db.session_ctx() as session:
            entries = WorldEntryRepository(session).list_by_category(project_id, None)
            chars = CharacterRepository(session).list_by_role(project_id)
            chapters = ChapterRepository(session).list_by_project(project_id)
        lines = ["【设定条目】"]
        lines.extend(f"- {e.title}：{(e.content or '')[:40]}" for e in entries[:30])
        lines.append("【角色】")
        lines.extend(f"- {c.name}（{c.role_type or '?'}）："
                     f"{str((c.profile or {}).get('background', ''))[:40]}" for c in chars[:15])
        lines.append("【章节摘要】")
        lines.extend(f"- 第{c.seq + 1}章「{c.title or ''}」：{(c.summary or '')[:50]}"
                     for c in chapters[:15] if c.summary)
        return "\n".join(lines)

    def _retrieve_world(self, project_id: int, chapter: Chapter | None,
                        extra_keywords: list[str] | None = None) -> str:
        keywords = self._chapter_keywords(chapter)
        for kw in (extra_keywords or []):
            kw = kw.strip()
            if kw and kw not in keywords:
                keywords.append(kw)
        if not keywords:
            return ""
        with self.db.session_ctx() as session:
            repo = WorldEntryRepository(session)
            seen: set[int] = set()
            uniq: list = []
            for kw in keywords:
                for e in repo.search(project_id, kw, limit=3):
                    if e.id not in seen:
                        seen.add(e.id)
                        uniq.append(e)
        return "\n".join(f"- {e.title}: {e.content[:150]}" for e in uniq[:5])

    def _retrieve_handbook(self, project_id: int, chapter: Chapter | None = None,
                           extra_keywords: list[str] | None = None) -> str:
        """教程知识库注入（M4）：按本章内容关键词检索相关切片（仅启用教程）。

        之前用固定关键词（正文/节奏/描写/对话/网文），与章节内容无关、经常注入不匹配的技巧；
        现在用本章标题/目标/节拍事件 + 策划步关键词检索，命中率与相关性都更高。
        """
        from app.services.handbook_service import HandbookService
        svc = HandbookService(self.db)
        keywords: list[str] = []
        if chapter is not None:
            if chapter.title:
                keywords.append(chapter.title)
            keywords.extend(self._chapter_keywords(chapter))
        for kw in (extra_keywords or []):
            kw = kw.strip()
            if kw and kw not in keywords:
                keywords.append(kw)
        if not keywords:
            keywords = ["正文", "节奏", "描写"]  # 无章节信息时的保底
        seen: set[int] = set()
        uniq: list = []
        for kw in keywords[:8]:
            for c in svc.search_chunks(kw, limit=3, project_id=project_id):
                if c.id not in seen:
                    seen.add(c.id)
                    uniq.append(c)
        return "\n\n".join(f"（{c.title or '教程片段'}）\n{c.content[:200]}" for c in uniq[:2])

    def _count_retrieved(self, project_id: int, chapter: Chapter | None) -> int:
        return len([x for x in self._retrieve_world(project_id, chapter).splitlines() if x])

    def _retrieve_arc(self, chapter: Chapter | None) -> str:
        if chapter is None or chapter.arc_id is None:
            return ""
        with self.db.session_ctx() as session:
            arc = ArcRepository(session).get(chapter.arc_id)
        if arc is None:
            return ""
        return "\n".join(x for x in (
            f"卷目标：{arc.goal}", f"核心冲突：{arc.conflict}", f"收束：{arc.resolution}"
        ) if x)

    def _retrieve_cast(self, project_id: int, chapter: Chapter | None) -> str:
        """出场角色卡注入：姓名/定位/性格/背景/动机/能力/说话风格。

        之前只带性格与说话风格——背景里的关键数值（如"平时成绩570-590"）进不了提示词，
        模型只能瞎编。背景/动机/能力各截断 120 字控制长度。
        """
        with self.db.session_ctx() as session:
            chars = CharacterRepository(session).list_by_role(project_id)
        lines = []
        for c in chars[:8]:
            p = c.profile or {}
            parts = [f"- {c.name}（{c.role_type or '?'}）"]
            if p.get("personality"):
                parts.append(f"性格：{p['personality']}")
            if p.get("background"):
                parts.append(f"背景：{str(p['background'])[:120]}")
            if p.get("motivation"):
                parts.append(f"动机：{str(p['motivation'])[:120]}")
            if p.get("ability"):
                parts.append(f"能力：{str(p['ability'])[:120]}")
            if p.get("speech_style"):
                parts.append(f"说话风格：{p['speech_style']}")
            lines.append("；".join(parts))
        return "\n".join(lines)

    def _beats_text(self, chapter: Chapter | None, target_beat: str) -> str:
        """本章细纲块：按「场景/出场人物/情节（按序）/对话要点/爽点/结尾钩子」结构组织
        （对齐本地微调模型的细纲→正文训练格式，见《提示词注入指南》）。"""
        if chapter is None or not chapter.beats_json:
            return f"（目标节拍：{target_beat}）" if target_beat else ""
        outline = chapter.outline_json or {}
        parts: list[str] = []
        if outline.get("scene"):
            parts.append(f"场景：{outline['scene']}")
        chars = outline.get("characters")
        if chars:
            if isinstance(chars, list):
                chars = "；".join(
                    f"{c.get('name', '')}（{c.get('role', '')}）" if isinstance(c, dict) else str(c)
                    for c in chars)
            parts.append(f"出场人物：{chars}")
        parts.append("情节（按顺序扩写）：")
        parts.extend(f"{i + 1}. {b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}"
                     for i, b in enumerate(chapter.beats_json))
        if outline.get("dialogue_hooks"):
            parts.append("对话要点："
                         + "；".join(str(x) for x in outline["dialogue_hooks"]))
        if outline.get("humor_points"):
            parts.append("爽点设计："
                         + "；".join(str(x) for x in outline["humor_points"]))
        if outline.get("ending_hook"):
            parts.append(f"结尾钩子：{outline['ending_hook']}")
        # 节拍锁定：只写目标节拍；后续节拍的事件/人物状态严禁提前（防"该醒的人提前醒"）
        if target_beat:
            cur_idx = None
            for i, b in enumerate(chapter.beats_json):
                ev = b.get("event", "")
                if target_beat == ev or (target_beat and target_beat in ev) or (ev and ev in target_beat):
                    cur_idx = i
                    break
            if cur_idx is not None:
                cur = chapter.beats_json[cur_idx]
                parts.append(
                    f"⚠️ 本次只写第 {cur_idx + 1} 拍：{cur.get('pov', '')}｜{cur.get('location', '')}｜{cur.get('event', '')}"
                    f"——场景锁定「{cur.get('location', '')}」，只出现本拍涉及的人物与状态")
                later = [b.get("event", "") for b in chapter.beats_json[cur_idx + 1:]
                         if b.get("event")]
                if later:
                    parts.append("🚫 后续节拍尚未发生（严禁提前写其中的人物登场/苏醒/揭示/事件）："
                                 + "；".join(later))
            else:
                parts.append(f"→ 待续写节拍：{target_beat}")
        return "\n".join(parts)

    def _chapter_keywords(self, chapter: Chapter | None) -> list[str]:
        """从章节目标/细纲提取检索词：切分为 4 字窗口（trigram 子串匹配友好）。"""
        parts: list[str] = []
        if chapter and chapter.objective:
            parts.append(chapter.objective)
        if chapter and chapter.beats_json:
            parts.extend(b.get("event", "") for b in chapter.beats_json[:3])
            parts.extend(b.get("location", "") for b in chapter.beats_json[:3])
        words: list[str] = []
        for p in parts:
            for w in self._windows(p, size=4, max_w=8):
                if w and w not in words:
                    words.append(w)
        return words[:6]

    @staticmethod
    def _windows(text: str, size: int = 4, max_w: int = 8) -> list[str]:
        """滑动窗口切分（用于无分词器环境下的中文子串检索词）。"""
        text = text.strip()
        if not text:
            return []
        if len(text) <= size:
            return [text]
        step = max(1, (len(text) - size) // max(max_w - 1, 1))
        return [text[i:i + size] for i in range(0, len(text) - size + 1, step)][:max_w]

    # ---------------- 工具 ----------------
    @staticmethod
    def _fmt(title: str, content: str) -> str:
        content = (content or "").strip()
        return f"{title}\n{content}" if content else ""

    @staticmethod
    def _clean_placeholders(text: str) -> str:
        """删除未填充的 {{占位符}} 与空区块标题行，保留任务指令与铁律。"""
        import re
        text = re.sub(r"\{\{\s*\w+\s*\}\}", "", text)
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.fullmatch(r"【[^】]*】", line):  # 空区块标题（如【风格指南】）
                continue
            out.append(line)
        return "\n".join(out)
