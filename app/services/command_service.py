"""AI 指令服务（设计文档 3.11 / 6.7.5）：自然语言指令 → 修改计划 → 确认后执行。"""
from __future__ import annotations

import re

from app.db.models import Arc, Chapter, Character, WorldEntry
from app.db.repositories.chapter_repo import ArcRepository, ChapterRepository, PlotRepository
from app.db.repositories.character_repo import CharacterRepository
from app.db.repositories.world_repo import WorldEntryRepository
from app.db.session import Database
from app.db.unit_of_work import UnitOfWork
from app.services.llm_service import LLMService


class CommandService:
    def __init__(self, db: Database, llm_service: LLMService):
        self.db = db
        self.llm = llm_service

    # ---------------- 计划（异步） ----------------
    async def plan(self, project_id: int, command: str) -> dict:
        """解析指令并产出【修改计划】JSON（只读，不写任何数据）。"""
        retrieved = self._retrieve(project_id, command)
        data = await self.llm.generate_structured(
            project_id=project_id,
            module="command",
            prompt_key="task.command_execute",
            system_prompt=self.llm.load_prompt("global.base_prompt", project_id),
            user_prompt=self.llm.load_prompt(
                "task.command_execute", project_id=project_id,
                user_command=command,
                retrieved_data=retrieved,
            ),
        )
        return data if isinstance(data, dict) else {}

    # ---------------- 执行（同步，仅执行被确认的变更项） ----------------
    def apply_plan(self, project_id: int, plan: dict, accept_indices: set[int]) -> dict:
        """按确认索引执行变更。返回 {applied, applied_notes, skipped:[原因]}。"""
        changes = plan.get("changes") or []
        applied = 0
        applied_notes: list[str] = []
        skipped: list[str] = []
        with self.db.session_ctx() as session:
            with UnitOfWork(session) as uow:
                for i, change in enumerate(changes):
                    if i not in accept_indices:
                        continue
                    ok, note = self._apply_one(session, project_id, change)
                    if ok:
                        applied += 1
                        applied_notes.append(note)
                    else:
                        skipped.append(note)
        return {"applied": applied, "applied_notes": applied_notes, "skipped": skipped}

    def _apply_one(self, session, project_id: int, change: dict) -> tuple[bool, str]:
        domain = change.get("domain", "")
        target = self._as_text(change.get("target")).strip()
        after = self._as_text(change.get("after"))
        before = self._as_text(change.get("before"))
        action = (change.get("action") or "update").lower()  # add / update / delete

        if domain == "world":
            if action == "add":
                # 新增设定归入"其他"分类（无则创建），避免在分类视图下看不到
                cat_id = self._ensure_other_category(session, project_id)
                entry = WorldEntryRepository(session).create(
                    project_id=project_id, category_id=cat_id,
                    title=target or (after[:20] or "新设定"),
                    content=after, importance=3,
                )
                return True, f"新增设定「{entry.title}」"
            entry = self._find_world_entry(session, project_id, target)
            if entry is None:
                return False, f"未找到设定条目「{target}」（若需新增，请在指令里说明'新增/添加'）"
            if action == "delete":
                WorldEntryRepository(session).delete(entry)
                return True, f"删除设定「{entry.title}」"
            if before and before in (entry.content or ""):
                entry.content = (entry.content or "").replace(before, after)
            else:
                entry.content = after
            return True, f"更新设定「{entry.title}」"

        if domain == "character":
            if action == "add":
                profile_data = change.get("profile") or {}
                if not isinstance(profile_data, dict):
                    profile_data = {}
                # 去重：同名角色已存在则合并更新，不重复创建
                existing = self._find_character(session, project_id, target)
                if existing is not None:
                    profile = dict(existing.profile or {})
                    for key, val in profile_data.items():
                        if val:
                            if profile.get(key):
                                profile[key] = (str(profile[key]) + "；" + str(val)).strip("；")
                            else:
                                profile[key] = val
                    if not profile_data and after:
                        # 仅文本描述：并入 background（而非 personality，避免背景塞性格栏）
                        prev = profile.get("background", "")
                        profile["background"] = (prev + "；" + after).strip("；")
                    existing.profile = profile
                    return True, f"角色「{existing.name}」已存在，已合并更新（未重复创建）"
                if profile_data:
                    c = CharacterRepository(session).create(
                        project_id=project_id, name=target or "新角色",
                        profile=profile_data, role_type=change.get("role_type", "support"),
                    )
                else:
                    c = CharacterRepository(session).create(
                        project_id=project_id, name=target or "新角色",
                        profile={"background": after}, role_type="support",
                    )
                return True, f"新增角色「{c.name}」"
            char = self._find_character(session, project_id, target)
            if char is None:
                return False, f"未找到角色「{target}」（若需新增，请说明'新增角色'）"
            if action == "delete":
                CharacterRepository(session).delete(char)
                return True, f"删除角色「{char.name}」"
            field = change.get("field", "")
            if not field:
                return False, "缺少 character 字段名（如 profile.personality）"
            if field.startswith("profile."):
                key = field.split(".", 1)[1]
                profile = dict(char.profile or {})
                profile[key] = after
                char.profile = profile
            elif field in ("name", "aliases", "role_type", "status"):
                setattr(char, field, after)
            else:
                return False, f"不支持的角色字段 {field}"
            return True, f"更新角色「{char.name}」.{field}"

        if domain == "plot":
            if action == "add":
                arc = self._find_arc(session, project_id, change.get("arc") or "") \
                    if change.get("arc") else None
                plot = self._find_or_create_plot(
                    session, project_id, arc.id if arc else None,
                    target or "新情节", summary=after,
                    conflict=self._as_text(change.get("conflict")),
                )
                res = self._as_text(change.get("resolution"))
                if res:
                    plot.resolution = res
                # 情节节拍线（跨章粗粒度推进点）
                beats = change.get("beats")
                if isinstance(beats, list) and beats:
                    from app.services.plot_service import PlotService
                    plot.beats_json = PlotService.normalize_beats(beats)
                return True, f"新增情节「{plot.title}」"
            plot = self._find_plot(session, project_id, target)
            if plot is None:
                return False, f"未找到情节「{target}」（若需新增，请说明'新增情节'）"
            if action == "delete":
                from app.db.repositories.chapter_repo import PlotRepository
                for ch in ChapterRepository(session).list_by_plot(plot.id):
                    ch.plot_id = None  # 章节保留但脱离情节
                PlotRepository(session).delete(plot)
                return True, f"删除情节「{plot.title}」"
            field = change.get("field", "summary") or "summary"
            if field in ("title", "summary", "conflict", "resolution"):
                setattr(plot, field, after)
                return True, f"更新情节「{plot.title}」.{field}"
            if field == "beats":
                from app.services.plot_service import PlotService
                raw = change.get("beats")
                if not isinstance(raw, list):
                    import json as _json
                    try:
                        raw = _json.loads(after)
                    except ValueError:
                        raw = None
                if not isinstance(raw, list):
                    return False, f"情节「{plot.title}」节拍线格式无法解析（需 JSON 数组）"
                plot.beats_json = PlotService.normalize_beats(raw)
                return True, f"更新情节「{plot.title}」节拍线（{len(plot.beats_json)} 条）"
            return False, f"不支持的情节字段 {field}（支持 title/summary/conflict/resolution/beats）"

        if domain == "chapter":
            if action == "add":
                # 细纲载荷（beats + scene/characters/ending_hook 等 extras）优先按细纲处理
                beats_payload, outline_payload = (
                    self._parse_outline_payload(change) if not change.get("field") else (None, None)
                )
                # 去重：同名章节已存在则合并目标/写入细纲，不重复创建
                existing = self._find_chapter(session, project_id, target)
                if existing is not None:
                    if beats_payload is not None:
                        existing.beats_json = beats_payload
                        existing.outline_status = "done" if beats_payload else existing.outline_status
                        self._merge_outline(existing, outline_payload)
                        return True, (f"章节「{existing.title or existing.seq + 1}」已存在，"
                                      f"已写入细纲（{len(beats_payload)} 个节拍，未重复创建）")
                    obj = existing.objective or ""
                    if after and after not in obj:
                        if obj and obj in after:
                            existing.objective = after  # 新内容更全（含旧目标），替换而非拼接
                        else:
                            existing.objective = (obj + "；" + after).strip("；")
                    return True, f"章节「{existing.title or existing.seq + 1}」已存在，已合并目标（未重复创建）"
                # 挂载卷/情节：change["arc"] 指定卷，change["plot_title"] 指定情节（不存在则创建）
                arc = self._find_arc(session, project_id, change.get("arc") or "") \
                    if change.get("arc") else None
                plot = None
                if change.get("plot_title"):
                    plot = self._find_or_create_plot(
                        session, project_id, arc.id if arc else None,
                        self._as_text(change.get("plot_title")) or "新情节",
                    )
                ch = ChapterRepository(session).create(
                    project_id=project_id, arc_id=arc.id if arc else None,
                    plot_id=plot.id if plot else None,
                    seq=ChapterRepository(session).next_seq(project_id),
                    title=target or "新章节",
                    objective="" if beats_payload is not None else after,
                )
                if beats_payload is not None:
                    ch.beats_json = beats_payload
                    ch.outline_status = "done" if beats_payload else "none"
                    self._merge_outline(ch, outline_payload)
                    return True, f"新增章节「{ch.title}」（含 {len(beats_payload)} 个节拍）"
                return True, f"新增章节「{ch.title}」"
            ch = self._find_chapter(session, project_id, target)
            if ch is None:
                return False, f"未找到章节「{target}」（若需新增，请说明'新增章节'）"
            if action == "delete":
                ChapterRepository(session).delete(ch)
                return True, f"删除章节「{ch.title or ch.seq + 1}」"
            field = change.get("field", "objective")
            if field in ("title", "objective"):
                setattr(ch, field, after)
            elif field == "arc":
                # 挂靠到卷：「把第N章归入第一卷」
                arc = self._find_arc(session, project_id, after)
                if arc is None:
                    return False, f"未找到卷「{after}」"
                ch.arc_id = arc.id
                return True, f"章节「{ch.title or ch.seq + 1}」已归入卷「{arc.title}」"
            elif field == "plot":
                # 挂靠到情节：「把第N章归入情节初遇」（同时自动归入情节所在的卷）
                plot = self._find_plot(session, project_id, after)
                if plot is None:
                    return False, f"未找到情节「{after}」（若需新增，请说明'新增情节'）"
                ch.plot_id = plot.id
                if plot.arc_id:
                    ch.arc_id = plot.arc_id
                return True, f"章节「{ch.title or ch.seq + 1}」已归入情节「{plot.title}」"
            elif field == "seq":
                import re as _re
                m = _re.search(r"\d+", after)
                if not m:
                    return False, f"章节序号必须是数字（收到：{after[:20]}）"
                ch.seq = int(m.group()) - 1  # 用户说的「序号 N」是 1 起始，存储为 0 起始
                return True, f"更新章节「{ch.title or ''}」序号为 {ch.seq + 1}"
            elif field == "beats":
                beats, outline_payload = self._parse_outline_payload(change)
                if beats is None:
                    return False, f"章节「{ch.title or ch.seq + 1}」细纲格式无法解析（需 JSON 数组或含 plot 的对象）"
                ch.beats_json = beats
                ch.outline_status = "done" if beats else "none"
                self._merge_outline(ch, outline_payload)
                return True, f"写入章节「{ch.title or ch.seq + 1}」细纲（{len(beats)} 个节拍）"
            elif field == "content":
                ch.content = after
                ChapterRepository(session).update_word_count(ch)
                if after.strip() and ch.content_status == "empty":
                    ch.content_status = "draft"
                return True, f"写入章节「{ch.title or ch.seq + 1}」正文（{ch.word_count} 字）"
            else:
                return False, f"不支持的章节字段 {field}（支持 title/objective/beats/content/seq/arc/plot）"
            return True, f"更新章节「{ch.title or ch.seq + 1}」.{field}"

        if domain == "outline":
            if action == "add":
                # 去重：同名卷已存在则更新目标/概要，不重复创建
                existing_arc = self._find_arc(session, project_id, target)
                if existing_arc is not None:
                    if after:
                        existing_arc.goal = after
                    if change.get("summary"):
                        existing_arc.summary = change["summary"]
                    return True, f"卷「{existing_arc.title}」已存在，已更新目标/概要（未重复创建）"
                arc = ArcRepository(session).create(
                    project_id=project_id, title=target or "新卷",
                    seq=ArcRepository(session).next_seq(project_id),
                    goal=after, summary=change.get("summary") or "",
                )
                return True, f"新增卷「{arc.title}」"
            arc = self._find_arc(session, project_id, target)
            if arc is None:
                return False, f"未找到卷「{target}」（若需新增，请说明'新增卷/生成大纲'）"
            if action == "delete":
                for ch in ChapterRepository(session).list_by_arc(arc.id):
                    ch.arc_id = None  # 章节保留但脱离该卷
                ArcRepository(session).delete(arc)
                return True, f"删除卷「{arc.title}」"
            field = change.get("field", "goal") or "goal"
            if field in ("title", "goal", "conflict", "resolution", "summary", "status"):
                setattr(arc, field, after)
            else:
                return False, f"不支持的卷字段 {field}"
            return True, f"更新卷「{arc.title}」.{field}"

        if domain == "beat":
            ch = self._find_chapter(session, project_id, target)
            if ch is None:
                return False, f"未找到章节「{target}」"
            idx = int(change.get("index", 0))
            beats = list(ch.beats_json or [])
            if action == "add":
                beats.append({"pov": "", "location": "", "event": after})
            elif not beats:
                return False, f"章节「{ch.title or ch.seq + 1}」暂无细纲（可改用 chapter 域 field=beats 整体写入）"
            elif not (0 <= idx < len(beats)):
                return False, f"节拍序号越界 {idx}"
            else:
                beats[idx] = dict(beats[idx])
                beats[idx]["event"] = after
            ch.beats_json = beats
            ch.outline_status = "done" if beats else "none"
            return True, f"更新章节「{ch.title or ch.seq + 1}」细纲"

        return False, f"不支持的修改域 {domain}（支持 world/character/chapter/outline/plot/beat）"

    # ---------------- 细纲解析（chapter field=beats） ----------------
    @staticmethod
    def _as_text(value) -> str:
        """把 change 字段统一为字符串：LLM 偶尔返回 list/dict（如细纲数组），序列化为 JSON 文本。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (list, dict)):
            import json as _json
            return _json.dumps(value, ensure_ascii=False)
        return str(value)

    # 细纲 extras 的合法字段（对齐 outlines_template.md：场景/人物/对话要点/爽点/结尾钩子/目标字数）
    OUTLINE_FIELDS = ("style_hint", "scene", "characters", "dialogue_hooks",
                      "humor_points", "ending_hook", "target_words")

    @staticmethod
    def _normalize_beats(raw) -> list[dict] | None:
        if not isinstance(raw, list):
            return None
        beats: list[dict] = []
        for b in raw:
            if isinstance(b, dict):
                event = str(b.get("event") or b.get("事件") or "").strip()
                if event:
                    beats.append({
                        "pov": str(b.get("pov") or ""),
                        "location": str(b.get("location") or ""),
                        "event": event,
                    })
            elif str(b).strip():
                beats.append({"pov": "", "location": "", "event": str(b).strip()})
        return beats

    @classmethod
    def _parse_outline_payload(cls, change: dict) -> tuple[list[dict] | None, dict | None]:
        """解析细纲载荷 → (beats, extras)。

        支持三种形态：
        ① change["beats"] 为数组 → 纯节拍；
        ② after 为 JSON 数组 → 纯节拍；
        ③ after 为含 plot 数组的 JSON 对象 → 节拍=plot，extras=白名单字段（scene/characters/…）。
        """
        import json as _json
        extras: dict | None = None
        if isinstance(change.get("outline"), dict):
            extras = dict(change["outline"])
        raw = change.get("beats")
        if raw is None and isinstance(change.get("after"), (list, dict)):
            raw = change["after"]  # LLM 把细纲直接放在 after 里
        if raw is None:
            text = cls._as_text(change.get("after")).strip()
            if text.startswith("```"):  # 宽容剥离代码围栏
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:]
            try:
                raw = _json.loads(text)
            except ValueError:
                raw = None
        if isinstance(raw, dict):
            if isinstance(raw.get("plot"), list):
                extras = {**{k: v for k, v in raw.items() if k in cls.OUTLINE_FIELDS},
                          **(extras or {})}
                raw = raw["plot"]
            else:
                raw = None
        beats = cls._normalize_beats(raw)
        if extras:
            extras = {k: v for k, v in extras.items()
                      if k in cls.OUTLINE_FIELDS and v not in (None, "", [])}
        return beats, (extras or None)

    @classmethod
    def _parse_beats(cls, change: dict) -> list[dict] | None:
        """兼容旧接口：仅取节拍列表。"""
        beats, _ = cls._parse_outline_payload(change)
        return beats

    @staticmethod
    def _merge_outline(chapter, extras: dict | None) -> None:
        """把细纲 extras 合并进 chapter.outline_json（空值不覆盖）。"""
        if not extras:
            return
        data = dict(chapter.outline_json or {})
        for k, v in extras.items():
            if v not in (None, "", []):
                data[k] = v
        chapter.outline_json = data

    # ---------------- 检索（供 plan 的上下文） ----------------
    def _retrieve(self, project_id: int, command: str) -> str:
        lines: list[str] = []
        with self.db.session_ctx() as session:
            from app.db.repositories.chapter_repo import PlotRepository
            # 卷
            arcs = ArcRepository(session).list_by_project(project_id)
            if arcs:
                lines.append("【卷】")
                lines.extend(f"- 第{a.seq + 1}卷「{a.title or ''}」｜目标：{(a.goal or '')[:60]}"
                             for a in arcs[:8])
            # 情节（卷→情节→章节 的中间层）
            plots = PlotRepository(session).list_by_project(project_id)
            if plots:
                lines.append("【情节】")
                for p in plots[:15]:
                    arc_tag = ""
                    if p.arc_id:
                        arc = next((a for a in arcs if a.id == p.arc_id), None)
                        arc_tag = f"（属：{arc.title or '第' + str(arc.seq + 1) + '卷'}）" if arc else ""
                    lines.append(
                        f"- 情节{p.seq + 1}「{p.title or ''}」{arc_tag}｜概要：{(p.summary or '')[:60]}")
            # 角色
            chars = CharacterRepository(session).list_by_role(project_id)
            if chars:
                lines.append("【角色】")
                lines.extend(f"- {c.name}（{c.role_type or '?'}）：{c.profile.get('personality', '')}" for c in chars[:10])
            # 章节
            chapters = ChapterRepository(session).list_by_project(project_id)
            if chapters:
                lines.append("【章节】")
                lines.extend(f"- 第{c.seq + 1}章 {c.title or ''}｜目标：{c.objective or ''}" for c in chapters[:15])
            # 设定：嵌入语义检索优先（配置了 [llm.embedding] 时），FTS/全量兜底
            keywords = command.strip()[:20]
            entries = []
            all_entries = WorldEntryRepository(session).list_by_category(project_id, None)[:30]
            if all_entries and keywords and self.llm is not None:
                from app.services.embedding_service import EmbeddingService
                emb = EmbeddingService(getattr(self.llm, "cfg", None))
                if emb.enabled:
                    ranked = emb.rank_texts(
                        command, [f"{e.title}：{(e.content or '')[:120]}" for e in all_entries],
                        top_k=8)
                    if ranked is not None:
                        entries = [all_entries[i] for i in ranked]
            if not entries and len(keywords) >= 3:
                entries = WorldEntryRepository(session).search(project_id, keywords, limit=6)
            if not entries:
                # 泛词/检索不到时返回全部设定（让 LLM 看到库里有什么，而不是误判"暂无数据"）
                entries = WorldEntryRepository(session).list_by_category(project_id, None)[:12]
            if entries:
                lines.append("【设定库】")
                lines.extend(f"- {e.title}: {e.content[:100]}" for e in entries)
        return "\n".join(lines) or "（无检索结果）"

    # ---------------- 查找（模糊匹配：精确 → 双向包含 → 去空格 → FTS） ----------------
    @staticmethod
    def _norm(s: str) -> str:
        return (s or "").replace(" ", "").replace("　", "")

    @staticmethod
    def _ensure_other_category(session, project_id: int) -> int | None:
        """确保"其他"分类存在，返回其 id（新增设定的默认归属）。"""
        from app.db.repositories.world_repo import WorldCategoryRepository
        repo = WorldCategoryRepository(session)
        for c in repo.list_by_project(project_id):
            if c.name == "其他":
                return c.id
        cat = repo.create(project_id=project_id, name="其他", sort_order=999)
        session.flush()
        return cat.id

    def _find_world_entry(self, session, project_id: int, target: str) -> WorldEntry | None:
        entries = WorldEntryRepository(session).list_by_project(project_id)
        t = self._norm(target)
        # ① 精确
        for e in entries:
            if self._norm(e.title) == t:
                return e
        # ② 双向包含（要求被匹配项非空，避免空串 "" in t 恒真误匹配）
        for e in entries:
            et = self._norm(e.title)
            if t and et and (t in et or et in t):
                return e
        # ③ FTS 检索兜底（前 3 字以上）
        if len(target) >= 3:
            hits = WorldEntryRepository(session).search(project_id, target[:20], limit=1)
            if hits:
                return hits[0]
        return None

    def _find_character(self, session, project_id: int, target: str) -> Character | None:
        chars = CharacterRepository(session).list_by_role(project_id)
        t = self._norm(target)
        for c in chars:
            if self._norm(c.name) == t:
                return c
        for c in chars:
            cn = self._norm(c.name)
            ca = self._norm(c.aliases)
            # 要求被匹配项非空（否则空别名 "" in t 恒真误匹配）
            if t and ((cn and (t in cn or cn in t)) or (ca and (t in ca or ca in t))):
                return c
        return None

    def _find_arc(self, session, project_id: int, target: str) -> Arc | None:
        """查找卷：标题模糊匹配优先，纯序号写法（「第2卷」「第二卷」）兜底。"""
        arcs = ArcRepository(session).list_by_project(project_id)
        t = self._norm(target)
        for a in arcs:
            at = self._norm(a.title)
            if t and at and (at == t or t in at or at in t):
                return a
        # 纯序号引用才按卷序号解析（避免「第一桶金」之类标题被误读为「第一」）
        m = re.fullmatch(rf"第?\s*{self._SEQ_PATTERN}\s*卷", target or "")
        if m:
            n = self._cn_int(m.group(1))
            if n and 0 < n <= len(arcs):
                return arcs[n - 1]
        return None

    def _find_chapter(self, session, project_id: int, target: str) -> Chapter | None:
        chapters = ChapterRepository(session).list_by_project(project_id)
        # ① 标题匹配优先（避免「第一桶金」之类标题被误读为「第一章」）
        t = self._norm(target)
        for c in chapters:
            ct = self._norm(c.title)
            if t and ct and (ct == t or t in ct or ct in t):
                return c
        # ② 纯序号引用兜底：「第3章」「第三章」「第3」「3」（兼容中文数字）
        m = re.fullmatch(rf"第?\s*{self._SEQ_PATTERN}\s*章?", target or "")
        if m:
            n = self._cn_int(m.group(1))
            if n and 0 < n <= len(chapters):
                return chapters[n - 1]
        return None

    def _find_or_create_plot(self, session, project_id: int, arc_id: int | None,
                             title: str, summary: str = "", conflict: str = ""):
        """按标题在卷内查找情节，不存在则创建。"""
        repo = PlotRepository(session)
        t = self._norm(title)
        plots = repo.list_by_arc(arc_id) if arc_id else repo.list_by_project(project_id)
        for p in plots:
            pt = self._norm(p.title)
            if t and pt and (t == pt or t in pt or pt in t):
                return p
        plot = repo.create(
            project_id=project_id, arc_id=arc_id,
            seq=repo.next_seq(arc_id) if arc_id else len(repo.list_by_project(project_id)),
            title=title or "新情节", summary=summary, conflict=conflict,
        )
        session.flush()
        return plot

    @staticmethod
    def _cn_int(text: str) -> int | None:
        """解析序号：支持阿拉伯数字与中文数字（一~二十）。"""
        if not text:
            return None
        if text.isdigit():
            return int(text)
        cn = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
        if text in cn:
            return cn[text]
        if text == "十":
            return 10
        if text.startswith("十") and len(text) == 2 and text[1] in cn:
            return 10 + cn[text[1]]  # 十一~十九
        if text.endswith("十") and len(text) == 2 and text[0] in cn:
            return cn[text[0]] * 10  # 二十~九十
        return None

    _SEQ_PATTERN = r"([0-9]+|十[一二三四五六七八九]|[一二两三四五六七八九十])"

    def _find_plot(self, session, project_id: int, target: str):
        """查找情节：情节名模糊匹配优先，再按「第一卷第一个情节」「第N情节」序号定位。"""
        plots = PlotRepository(session).list_by_project(project_id)
        # ① 情节名匹配优先（剥掉「情节」「第一卷的」等前缀）
        t = self._norm(target)
        t = re.sub(r"^(情节|第.+卷[的之]?)", "", t)
        for p in plots:
            pt = self._norm(p.title)
            if t and pt and (t == pt or t in pt or pt in t):
                return p
        seq = self._SEQ_PATTERN
        # ② 「第X卷第Y个情节」：按卷过滤 + 卷内序号（兼容中文数字）
        m = re.search(rf"第\s*{seq}\s*卷.{{0,6}}第\s*{seq}\s*(?:个)?情节", target or "")
        if m:
            arcs = ArcRepository(session).list_by_project(project_id)
            ai_n, pi_n = self._cn_int(m.group(1)), self._cn_int(m.group(2))
            if ai_n and pi_n:
                ai, pi = ai_n - 1, pi_n - 1
                if 0 <= ai < len(arcs):
                    of_arc = [p for p in plots if p.arc_id == arcs[ai].id]
                    if 0 <= pi < len(of_arc):
                        return of_arc[pi]
        # ③ 纯序号引用兜底：「第一个情节」「第2情节」
        m = re.fullmatch(rf"第\s*{seq}\s*(?:个)?情节", target or "")
        if m:
            n = self._cn_int(m.group(1))
            if n and 0 < n <= len(plots):
                return plots[n - 1]
        return None
