"""Worker 基类：仅通过工具注册表调用只读工具取数（不 import repositories、不持有 session），
递归上限读 user_settings.recursive_limit，硬上限 + 超时保护。提供 tool-calling loop。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.models import WorkerMetadata
from app.agents.skills.skill_manager import SkillManager, get_skill_manager
from app.agents.tools import call_tool, tool_schemas
from app.config import settings as app_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)


class WorkerBase:
    worker_name: str = "base"
    metadata: WorkerMetadata | None = None

    def __init__(
        self,
        db: AsyncSession,
        llm,
        recursive_limit: int,
        metadata: WorkerMetadata | None = None,
        timeout: float = 60.0,
    ):
        self.db = db
        self.llm = llm
        self.metadata = metadata
        self.timeout = timeout
        effective_limit = metadata.recursive_limit if metadata and metadata.recursive_limit else recursive_limit
        self.recursive_limit = min(max(effective_limit, 1), app_settings.recursive_limit_hard_cap)

    async def run(self, task, context, history_context=None) -> dict:
        """Default JSON-driven run.

        Args:
            task: Task object with worker, goal, input_artifacts, meta.
            context: HarnessContext with entities, project_summary, etc.
            history_context: Optional chat history for LLM.
        Returns:
            dict with {"summary", "changes", "artifacts", "notes", "stage", "error"}.
        """
        if self.metadata is None:
            raise RuntimeError(f"Worker {self.worker_name} has no metadata")

        system_prompt = self.metadata.system_prompt
        # Inject output schema into prompt if available
        if self.metadata.output_schema:
            schema_text = json.dumps(self.metadata.output_schema, ensure_ascii=False, indent=2)
            system_prompt += (
                f"\n\n你必须按以下 JSON schema 输出：\n{schema_text}\n只输出 JSON，"
                "不要 markdown 代码块，不要解释。"
            )

        system_prompt = await self._inject_skills(system_prompt, task)

        user_prompt = self._build_user_prompt(task, context)
        raw = await self._tool_loop(
            system_prompt,
            user_prompt,
            extra_tools=None,
            history_context=history_context,
        )
        return self._normalize_result(raw)

    async def _inject_skills(self, system_prompt: str, task) -> str:
        """Append inline and RAG skill references to the system prompt."""
        if not system_prompt:
            system_prompt = ""
        skill_manager = get_skill_manager()
        try:
            skill_texts = skill_manager.get_skills_for_worker(
                self.worker_name,
                worker_skills=self.metadata.skills if self.metadata else [],
                task_goal=getattr(task, "goal", ""),
            )
            if skill_texts:
                system_prompt += "\n\n【创作方法论参考】\n"
                for cfg, content in skill_texts:
                    system_prompt += f"\n--- {cfg.skill_name} ---\n{content}\n"
        except Exception:
            logger.exception("Failed to inject inline skills for %s", self.worker_name)

        try:
            rag_results = await skill_manager.query_rag_skills(
                self.db,
                self.worker_name,
                rag_skill_names=self.metadata.rag_skills if self.metadata else [],
                query=getattr(task, "goal", ""),
            )
            if rag_results:
                system_prompt += "\n\n【相关案例参考】\n"
                for r in rag_results:
                    system_prompt += f"\n--- {r.chunk_path} ---\n{r.chunk_text}\n"
        except Exception:
            logger.exception("Failed to inject RAG skills for %s", self.worker_name)
        return system_prompt

    def _build_user_prompt(self, task, context) -> str:
        parts = [f"【用户目标】\n{task.goal}"]
        if task.input_artifacts:
            parts.append(f"【输入产物】\n{json.dumps(task.input_artifacts, ensure_ascii=False)}")
        parts.append(f"【项目摘要】\n{context.project_summary or '未提供'}")
        entities_text = self._render_entities(context)
        if entities_text:
            parts.append(f"【项目实体】\n{entities_text}")
        return "\n\n".join(parts)

    def _render_entities(self, context) -> str:
        lines = []
        for entity_type, entities in context.entities.items():
            if not entities:
                continue
            lines.append(f"[{entity_type}]")
            for e in entities:
                lines.append(json.dumps(e, ensure_ascii=False))
        return "\n".join(lines)

    def _normalize_result(self, raw: dict) -> dict:
        """Ensure result has the canonical keys."""
        if isinstance(raw, str):
            return {"summary": raw, "changes": [], "artifacts": {}, "notes": [], "stage": self.worker_name}
        return {
            "summary": raw.get("summary", ""),
            "changes": raw.get("changes") or [],
            "artifacts": raw.get("artifacts") or {},
            "notes": raw.get("notes") or [],
            "stage": raw.get("stage") or self.worker_name,
            "error": raw.get("error"),
        }

    async def _tool_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        extra_tools: list[dict] | None = None,
        history_context: list[dict] | None = None,
    ) -> dict:
        """标准 tool-calling 循环：LLM 可多次调用只读工具取数，最终产出结构化结果。"""
        messages = [{"role": "system", "content": system_prompt}]
        if history_context:
            messages.extend(history_context)
        messages.append({"role": "user", "content": user_prompt})
        schemas = tool_schemas() + (extra_tools or [])
        if self.metadata and self.metadata.tools:
            allowed = set(self.metadata.tools)
            schemas = [s for s in schemas if s.get("name") in allowed] + (extra_tools or [])
        calls = 0
        start = time.time()
        while calls < self.recursive_limit:
            if time.time() - start > self.timeout:
                break
            calls += 1
            resp = await self.llm.chat(
                messages, response_format=None
            )
            logger.warning("[%s] LLM resp (call %d): %s", self.worker_name, calls, resp[:500])
            # 尝试解析工具调用（兼容 OpenAI function call / JSON 指令两种形态）
            tool_call = self._parse_tool_call(resp)
            if not tool_call:
                # 没有进一步工具调用 -> 视为最终产出
                parsed = self._parse_final(resp)
                # 若最终输出不是合法结构化结果，尝试用 json_object 模式强制 JSON
                if not self._is_structured(parsed):
                    try:
                        forced = await self.llm.parse_llm_json(messages)
                        if isinstance(forced, dict):
                            parsed = forced
                        elif isinstance(forced, list):
                            parsed = {"changes": forced}
                        else:
                            parsed = self._parse_final(str(forced))
                    except AppError:
                        # 配置类错误（如缺 API key）必须上抛，不能吞成"解析失败"
                        raise
                    except Exception:
                        pass
                logger.warning("[%s] parsed final: %s", self.worker_name, parsed)
                return parsed
            name = tool_call.get("name")
            args = tool_call.get("arguments", {})
            logger.warning("[%s] tool call: %s args: %s", self.worker_name, name, args)
            try:
                result = await call_tool(self.db, name, args)
            except Exception as e:
                result = {"error": str(e)}
            logger.warning("[%s] tool result: %s", self.worker_name, str(result)[:500])
            messages.append({"role": "assistant", "content": resp})
            messages.append({
                "role": "user",
                "content": f"工具 {name} 返回结果：\n{str(result)[:4000]}",
            })
        # 超出上限，让 LLM 直接总结
        final = await self.llm.chat(messages)
        logger.warning("[%s] final LLM resp: %s", self.worker_name, final[:500])
        parsed = self._parse_final(final)
        if not self._is_structured(parsed):
            try:
                forced = await self.llm.parse_llm_json(messages)
                if isinstance(forced, dict):
                    parsed = forced
                elif isinstance(forced, list):
                    parsed = {"changes": forced}
                else:
                    parsed = self._parse_final(str(forced))
            except AppError:
                raise
            except Exception:
                pass
        logger.warning("[%s] parsed final: %s", self.worker_name, parsed)
        return parsed

    def _parse_tool_call(self, text: str) -> dict | None:
        # 期望格式：TOOL_CALL:{"name": "...", "arguments": {...}}
        marker = "TOOL_CALL:"
        if marker in text:
            part = text.split(marker, 1)[1]
            try:
                return json.loads(part.strip())
            except Exception:
                return None
        return None

    def _parse_final(self, text: str) -> dict:
        """将 LLM 最终文本解析为结构化结果；支持纯 JSON、Markdown 代码块、JSON 子串。"""
        cleaned = text.strip()

        def try_parse(value: str) -> dict | None:
            try:
                parsed = json.loads(value.strip().strip("`"))
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"changes": parsed}
            except (json.JSONDecodeError, TypeError):
                pass
            return None

        # 1. 尝试解析整段文本
        result = try_parse(cleaned)
        if result is not None:
            return result

        # 2. 查找 ```json / ``` 代码块
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts[1:]:
                block = part.strip()
                if block.lower().startswith("json"):
                    block = block[4:]
                result = try_parse(block)
                if result is not None:
                    return result

        # 3. 尝试截取第一个 { ... } 或 [ ... ] 子串
        for start_char, end_char in (("{", "}"), ("[", "]")):
            start = cleaned.find(start_char)
            if start != -1:
                end = cleaned.rfind(end_char)
                if end > start:
                    result = try_parse(cleaned[start:end + 1])
                    if result is not None:
                        return result

        return {"raw": text}

    @staticmethod
    def _is_structured(parsed: dict) -> bool:
        """判断 _parse_final 的结果是否为合法结构化产出（而非 fallback 的 raw 文本）。"""
        if not isinstance(parsed, dict):
            return False
        # 包含 changes 或其他结构化字段即视为合法
        if "changes" in parsed or "action" in parsed:
            return True
        return False


async def run_worker(
    worker_cls: type["WorkerBase"],
    db: AsyncSession,
    llm,
    recursive_limit: int,
    task,
    context,
    metadata: WorkerMetadata | None = None,
    history_context: list[dict] | None = None,
) -> dict:
    if metadata is None:
        metadata = _load_worker_metadata(worker_cls)
    worker = worker_cls(db, llm, recursive_limit, metadata=metadata)
    return await worker.run(task, context, history_context=history_context)


def _load_worker_metadata(worker_cls: type["WorkerBase"]) -> WorkerMetadata:
    """Load WorkerMetadata from the worker's JSON config file.

    Config files live at app/agents/harness/workers/configs/<worker_name>.json.
    """
    worker_name = worker_cls.worker_name
    config_path = Path(__file__).parent / "workers" / "configs" / f"{worker_name}.json"
    if not config_path.exists():
        raise RuntimeError(f"No metadata config found for worker '{worker_name}' at {config_path}")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return WorkerMetadata(**raw)
