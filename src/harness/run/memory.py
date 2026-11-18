"""工作记忆：四层结构 + 事件回放重建。

四层：decided（用户指令/审批结论，永不淘汰）/ summary（滚动摘要）/
done（已完成章节）/ detail（最近几步）。detail 超 DETAIL_CAP 条时，
最旧条目原文压入 summary 尾部再淘汰——P1 降级为纯文本折叠，
LLM lite 压缩（task.work_memory_summarize）留 P4。
"""

DETAIL_CAP = 20

_DECIDED_KINDS = ("user_choice", "plan_approved", "plan_rejected")
_DETAIL_KINDS = ("agent_step", "tool_result")


class WorkMemory:
    def __init__(self) -> None:
        self.decided: list[str] = []
        self.summary: list[str] = []
        self.done: list[str] = []
        self.detail: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ 记录

    def record(self, event: dict) -> None:
        """按 kind 归类写入对应层；detail 溢出时最旧条目原文折叠进 summary。"""
        kind = event.get("kind", "")
        payload = event.get("payload") or {}
        if kind == "run_started":
            text = str(payload.get("raw", ""))
            if text:  # 畸形事件空串防护（Task 12 挂点）：空 brief 不进 decided
                self.decided.append(text)
        elif kind in _DECIDED_KINDS:
            self.decided.append(f"{payload.get('question', '')} → {payload.get('picked', '')}")
        elif kind == "chapter_done":
            self.done.append(str(payload.get("title") or payload.get("chapter") or ""))
        elif kind in _DETAIL_KINDS:
            label = payload.get("action") or payload.get("tool") or ""
            digest = payload.get("thought") or payload.get("observation") or ""
            self.detail.append((f"{kind}:{label}", str(digest)))
            if len(self.detail) > DETAIL_CAP:
                old_kind, old_digest = self.detail.pop(0)
                self.summary.append(f"{old_kind} {old_digest}")

    # ------------------------------------------------------------------ 回放

    @classmethod
    def rebuild(cls, events: list[dict]) -> "WorkMemory":
        """顺序 replay 事件列表，重建工作记忆。"""
        mem = cls()
        for event in events:
            mem.record(event)
        return mem

    # ------------------------------------------------------------------ 渲染

    def render(self) -> str:
        """四节文本块：【已定】/【摘要】/【已完成】/【近况】，空节省略。"""
        blocks = []
        if self.decided:
            blocks.append("【已定】\n" + "\n".join(f"- {d}" for d in self.decided))
        if self.summary:
            blocks.append("【摘要】\n" + "\n".join(f"- {s}" for s in self.summary))
        if self.done:
            blocks.append("【已完成】\n" + "\n".join(f"- {d}" for d in self.done))
        if self.detail:
            blocks.append("【近况】\n" + "\n".join(f"- {k} {v}" for k, v in self.detail))
        return "\n\n".join(blocks)
