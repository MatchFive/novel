"""章节正文生成工作流（LangGraph）：上下文构造 → 正文写作 → 逻辑与内容审核 → 修正循环。

替代单次 LLM 调用：上下文构造（角色记忆/世界观/知识状态/承诺/前情/细纲）→
正文写作 → 审核（设定/记忆/知识泄漏/逻辑）→ 条件边（通过→结束 / 不通过→修正→再审，限 2 次）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.db.models import Chapter
from app.db.session import Database
from app.llm.assembler import ContextAssembler
from app.services.llm_service import LLMService

MAX_REVISE = 2


class DraftState(TypedDict, total=False):
    project_id: int
    chapter: Chapter
    gen_config: dict
    target_beat: str
    recent_draft: str
    recent_summaries: str
    extra_keywords: list[str]      # 上下文策划步产出的检索词
    web_facts: str                 # 联网核查结果（现实知识校准）
    system_prompt: str
    user_prompt: str
    injected: dict
    context_materials: dict     # 审核用素材（world/pov_memories/knowledge）
    draft: str
    pass_review: bool
    issues: list[dict]
    revise_count: int
    tier_override: str | None


@dataclass
class DraftResult:
    draft: str
    pass_review: bool
    issues: list[dict] = field(default_factory=list)
    revise_count: int = 0
    injected: dict = field(default_factory=dict)


class DraftWorkflow:
    """章节正文生成流水线（LangGraph StateGraph + 条件边）。"""

    def __init__(self, db: Database, llm: LLMService):
        self.db = db
        self.llm = llm
        self._graph = self._build()

    def _build(self):
        g = StateGraph(DraftState)
        g.add_node("plan_context", self._plan_context)
        g.add_node("collect_context", self._collect_context)
        g.add_node("write_draft", self._write_draft)
        g.add_node("review", self._review)
        g.add_node("revise", self._revise)
        g.set_entry_point("plan_context")
        g.add_edge("plan_context", "collect_context")
        g.add_edge("collect_context", "write_draft")
        g.add_edge("write_draft", "review")
        # 条件边：审核通过 / 修正次数到上限 → 结束；否则修正
        g.add_conditional_edges(
            "review",
            self._route_after_review,
            {"pass": END, "revise": "revise"},
        )
        g.add_edge("revise", "review")
        return g.compile()

    # ---------------- 路由 ----------------
    @staticmethod
    def _route_after_review(state: DraftState) -> str:
        if state.get("pass_review"):
            return "pass"
        if state.get("revise_count", 0) >= MAX_REVISE:
            return "pass"  # 到上限也结束（避免死循环）
        return "revise"

    # ---------------- 节点 ----------------
    async def _plan_context(self, state: DraftState) -> DraftState:
        """写作前上下文策划（模型驱动）：看细纲+资源目录，挑选要注入的设定/角色/检索词，
        涉及现实知识时给出联网核查问题（结果注入【现实知识核查】块）。

        走 lite 档，可在「模型与路由设置」指派给本地模型；失败不影响主流程。
        """
        chapter = state["chapter"]
        try:
            assembler = ContextAssembler(self.db)
            beats_text = "\n".join(
                f"{i + 1}. {b.get('event', '')}" for i, b in enumerate(chapter.beats_json or [])
            ) or "（无细纲）"
            data = await self.llm.generate_structured(
                project_id=state["project_id"], module="draft",
                prompt_key="task.context_select",
                system_prompt=self.llm.load_prompt("global.base_prompt", state["project_id"]),
                user_prompt=self.llm.load_prompt(
                    "task.context_select", project_id=state["project_id"],
                    objective=chapter.objective or chapter.title or "",
                    beats=beats_text,
                    target_beat=state.get("target_beat", ""),
                    catalog=assembler.catalog(state["project_id"]),
                ),
                retries=0, caller="DraftWorkflow.plan_context",
            )
            keywords = [str(k).strip() for k in (data or {}).get("keywords") or [] if str(k).strip()]
            # 策划选中的设定条目也作为检索词（标题参与 FTS 命中）
            keywords += [str(t).strip() for t in (data or {}).get("world_titles") or [] if str(t).strip()]
            web_facts = ""
            need_web = str((data or {}).get("need_web") or "").strip()
            if need_web:
                from app.utils.web_search import web_search_logged
                web_facts = web_search_logged(need_web, data_dir=self.llm.cfg.data_dir)
            return {**state, "extra_keywords": keywords[:10], "web_facts": web_facts}
        except Exception:
            return {**state, "extra_keywords": [], "web_facts": ""}

    async def _collect_context(self, state: DraftState) -> DraftState:
        """上下文构造：角色记忆/世界观/知识状态/承诺/前情/细纲/最近正文（ContextAssembler）。"""
        assembler = ContextAssembler(self.db)
        chapter = state["chapter"]
        gen_config = state.get("gen_config", {})
        extra_kws = state.get("extra_keywords") or []
        memory = assembler.memory.inject_draft_memory(
            project_id=state["project_id"], chapter=chapter,
            pov_char_id=chapter.pov_char_id,
            keywords=[state.get("target_beat", ""), chapter.objective or ""] + extra_kws,
            memory_strength=gen_config.get("memory_strength", "standard"),
        )
        result = assembler.build_draft_prompt(
            project_id=state["project_id"], chapter=chapter, gen_config=gen_config,
            recent_draft=state.get("recent_draft", ""),
            target_beat=state.get("target_beat", ""),
            recent_summaries=state.get("recent_summaries", ""),
            memory=memory,
            extra_keywords=extra_kws,
            web_facts=state.get("web_facts", ""),
        )
        tier_override = gen_config.get("tier_override")
        if tier_override is None and gen_config.get("quality_mode") == "refined":
            tier_override = "pro"
        return {
            **state,
            "system_prompt": result.system_prompt,
            "user_prompt": result.user_prompt,
            "injected": result.injected,
            "tier_override": tier_override,
            "context_materials": {
                "world_entries": assembler._retrieve_world(state["project_id"], chapter),
                "pov_memories": memory.pov_memories,
                "knowledge_state": memory.knowledge_state,
                "cast_cards": assembler._retrieve_cast(state["project_id"], chapter),
                "outline": assembler._beats_text(chapter, ""),  # 审核要对齐细纲
            },
        }

    async def _write_draft(self, state: DraftState) -> DraftState:
        """正文写作：流式生成正文（过程实时上报，供 UI 流式上屏）。"""
        self._report("writing", "")
        text = ""
        last_report = 0
        async for chunk in self.llm.generate_stream(
            project_id=state["project_id"], module="draft",
            prompt_key="task.draft_continue",
            system_prompt=state["system_prompt"],
            user_prompt=state["user_prompt"],
            tier_override=state.get("tier_override"),
            caller="DraftWorkflow.write_draft",
        ):
            text += chunk
            if len(text) - last_report >= 120:  # 节流：每 ~120 字上报一次
                last_report = len(text)
                self._report("writing", text)
        return {**state, "draft": text}

    async def _review(self, state: DraftState) -> DraftState:
        """逻辑与内容审核：设定/记忆/知识泄漏/逻辑。"""
        self._report("review", "")
        cm = state.get("context_materials", {})
        data = await self.llm.generate_structured(
            project_id=state["project_id"], module="draft", prompt_key="task.draft_review",
            system_prompt=self.llm.load_prompt("global.base_prompt", state["project_id"]),
            user_prompt=self.llm.load_prompt(
                "task.draft_review", project_id=state["project_id"],
                world_entries=cm.get("world_entries", ""),
                pov_memories=cm.get("pov_memories", ""),
                knowledge_state=cm.get("knowledge_state", ""),
                cast_cards=cm.get("cast_cards", ""),
                outline=cm.get("outline", ""),
                draft=state.get("draft", "")[-6000:],
            ),
            caller="DraftWorkflow.review",
        )
        issues = (data or {}).get("issues") or []
        pass_review = bool((data or {}).get("pass", not issues))
        return {**state, "issues": issues, "pass_review": pass_review}

    async def _revise(self, state: DraftState) -> DraftState:
        """按审核意见修正正文。"""
        self._report("revising", state.get("draft", ""))
        cm = state.get("context_materials", {})
        issues_text = "\n".join(
            f"· [{i.get('type','')}] {i.get('issue','')} → {i.get('suggestion','')}"
            for i in state.get("issues", [])
        )
        text = ""
        async for chunk in self.llm.generate_stream(
            project_id=state["project_id"], module="draft", prompt_key="task.draft_revise",
            system_prompt=self.llm.load_prompt("global.base_prompt", state["project_id"]),
            user_prompt=self.llm.load_prompt(
                "task.draft_revise", project_id=state["project_id"],
                issues=issues_text,
                world_entries=cm.get("world_entries", ""),
                pov_memories=cm.get("pov_memories", ""),
                knowledge_state=cm.get("knowledge_state", ""),
                cast_cards=cm.get("cast_cards", ""),
                draft=state.get("draft", ""),
            ),
            tier_override=state.get("tier_override"),
            caller="DraftWorkflow.revise",
        ):
            text += chunk
        original = state.get("draft", "")
        # 截断防护：修正稿明显短于原稿（<70%）大概率是 max_tokens 截断的残稿——保留原稿，不用残稿替换
        if text and original and len(text) < len(original) * 0.7:
            from app.core.logger import get_logger
            get_logger("draft_workflow").warning(
                "修正稿疑似被截断（%d 字 < 原稿 %d 字的 70%%），保留原稿", len(text), len(original))
            text = original
        return {**state, "draft": text or original,
                "revise_count": state.get("revise_count", 0) + 1}

    # ---------------- 对外接口 ----------------
    def _report(self, phase: str, text: str) -> None:
        """向 UI 上报流水线进度（phase: writing/review/revising；text: writing 阶段的当前草稿）。"""
        cb = getattr(self, "_on_progress", None)
        if cb:
            try:
                cb(phase, text)
            except Exception:
                pass  # 进度回调失败不能影响生成主流程

    async def run(self, *, project_id: int, chapter: Chapter, gen_config: dict,
                  target_beat: str = "", recent_draft: str = "",
                  recent_summaries: str = "", on_progress=None) -> DraftResult:
        """运行正文生成流水线，返回正文 + 审核结果。on_progress(phase, text) 接收进度。"""
        self._on_progress = on_progress
        final = await self._graph.ainvoke({
            "project_id": project_id, "chapter": chapter, "gen_config": gen_config,
            "target_beat": target_beat, "recent_draft": recent_draft,
            "recent_summaries": recent_summaries, "revise_count": 0,
        })
        return DraftResult(
            draft=final.get("draft", ""),
            pass_review=final.get("pass_review", True),
            issues=final.get("issues", []),
            revise_count=final.get("revise_count", 0),
            injected=final.get("injected", {}),
        )
