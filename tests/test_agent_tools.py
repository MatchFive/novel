"""实体 Agent 工具循环 + 整卷拆章测试（mock LLM，不调真实模型）。运行：python tests/test_agent_tools.py"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


class ToolCallingLLM:
    """模拟会先调工具再给变更的 LLM。"""

    def __init__(self):
        self.prompts: list[str] = []

    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        prompt = kwargs.get("user_prompt", "")
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return {"tool": "list_chapters"}  # 第一轮：先查章节
        return {"changes": [{"domain": "chapter", "target": "第1章",
                             "field": "objective", "after": "新目标"}]}


class PlanLLM:
    """整卷拆章 mock：plot_plan 返回情节线，chapter_plan 返回两章完整细纲。"""

    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        if kwargs.get("prompt_key") == "task.plot_plan":
            return {"plots": [
                {"title": "穿越落地", "summary": "刘修穿越异世界",
                 "conflict": "生存危机", "resolution": "遇见女神"},
            ]}
        return {"chapters": [
            {"plot_title": "穿越落地", "title": "618分", "objective": "开局狂喜",
             "scene": "地球·家中卧室", "characters": [{"name": "刘修", "role": "主角"}],
             "plot": [{"pov": "刘修", "location": "卧室", "event": "查分狂喜"},
                      {"pov": "刘修", "location": "卧室", "event": "疲惫入睡"}],
             "dialogue_hooks": ["我考上了！"], "humor_points": ["超常发挥"],
             "ending_hook": "意识被抽离", "target_words": 2800},
            {"plot_title": "穿越落地", "title": "异世界醒来", "objective": "穿越落地",
             "scene": "异世界·贵族卧室",
             "plot": [{"pov": "刘修", "location": "贵族卧室", "event": "醒来发现女神像"}],
             "ending_hook": "女神开口"},
        ]}


def main() -> int:
    try:
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        db.init_schema()
        seed_defaults(db)
        p = ProjectService(db).create(name="t", genre="玄幻", logline="l")
        chs = ChapterService(db)
        chs.create(project_id=p.id, title="旧章", objective="旧目标")

        # ---- 工具循环：第一轮调 list_chapters，第二轮产出 changes ----
        from app.workflows.agent_orchestrator import ChapterAgent
        llm = ToolCallingLLM()
        progress: list[str] = []
        agent = ChapterAgent(db, llm, on_progress=progress.append)
        changes = asyncio.run(agent.handle(p.id, {"action": "update", "description": "改第1章目标"}, ""))
        assert len(llm.prompts) == 2, "应经历 工具调用→产出 两轮"
        assert "【工具 list_chapters 返回】" in llm.prompts[1], "第二轮应带着工具结果"
        assert "旧章" in llm.prompts[1], "工具结果应包含章节列表"
        assert changes and changes[0]["domain"] == "chapter", changes
        assert any("list_chapters" in m for m in progress), f"工具调用应播报进度: {progress}"
        print("agent tool loop + progress OK")

        # ---- tool_calls 数组格式兼容（deepseek 风格批量调用） ----
        class BatchToolLLM(ToolCallingLLM):
            async def generate_structured(self, **kwargs):
                self.prompts.append(kwargs.get("user_prompt", ""))
                if len(self.prompts) == 1:
                    return {"tool_calls": [{"tool": "list_chapters", "args": {}},
                                           {"tool": "get_chapter", "args": {"target": "第1章"}}]}
                return {"changes": [{"domain": "chapter", "target": "第1章",
                                     "field": "objective", "after": "x"}]}

        llm2 = BatchToolLLM()
        agent2 = ChapterAgent(db, llm2)
        changes2 = asyncio.run(agent2.handle(p.id, {"action": "update", "description": "x"}, ""))
        assert "【工具 get_chapter 返回】" in llm2.prompts[1], "tool_calls 数组应逐个执行"
        assert changes2, changes2
        print("tool_calls array compat OK")

        # ---- 整卷拆章：plan_chapters 两段式（情节线→章节）→ apply 入库（含 beats + outline_json） ----
        ol = OutlineService(db)
        arc = ol.create(project_id=p.id, title="开荒期", goal="立足异世界")
        plots, chapters = asyncio.run(ol.plan_chapters(arc.id, PlanLLM()))
        assert len(plots) == 1 and plots[0]["title"] == "穿越落地", plots
        assert len(chapters) == 2 and chapters[0]["plot"], chapters
        print("plan_chapters (plot->chapter) OK")

        svc = CommandService(db, None)
        changes = [{
            "domain": "plot", "action": "add", "target": p["title"],
            "after": p.get("summary", ""), "conflict": p.get("conflict", ""),
            "resolution": p.get("resolution", ""), "arc": "开荒期",
        } for p in plots]
        changes += [{
            "domain": "chapter", "action": "add", "target": c["title"],
            "after": c.get("objective", ""), "beats": c.get("plot"),
            "plot_title": c.get("plot_title", ""), "arc": "开荒期",
            "outline": {k: c[k] for k in CommandService.OUTLINE_FIELDS if c.get(k)},
        } for c in chapters]
        r = svc.apply_plan(p.id, {"changes": changes}, set(range(len(changes))))
        assert r["applied"] == 3, r
        all_ch = [c for c in chs.list(p.id) if c.title in ("618分", "异世界醒来")]
        assert len(all_ch) == 2
        c1 = next(c for c in all_ch if c.title == "618分")
        assert len(c1.beats_json) == 2 and c1.outline_status == "done"
        assert c1.outline_json["scene"] == "地球·家中卧室"
        assert c1.outline_json["ending_hook"] == "意识被抽离"
        assert c1.outline_json["humor_points"] == ["超常发挥"]
        # 章节应挂载到情节与卷
        assert c1.plot_id is not None and c1.arc_id == arc.id
        from app.services.plot_service import PlotService
        plot = PlotService(db).get(c1.plot_id)
        assert plot.title == "穿越落地" and plot.arc_id == arc.id
        print("chapter add with plot mounting OK")

        # ---- field=beats 含 plot 对象的完整细纲结构 ----
        r = svc.apply_plan(p.id, {"changes": [{
            "domain": "chapter", "action": "update", "target": "异世界醒来",
            "field": "beats",
            "after": {"scene": "女神殿", "plot": [{"event": "新节拍"}],
                      "ending_hook": "女神现身"}}]}, {0})
        assert r["applied"] == 1, r
        c2 = next(c for c in chs.list(p.id) if c.title == "异世界醒来")
        assert [b["event"] for b in c2.beats_json] == ["新节拍"]
        assert c2.outline_json["scene"] == "女神殿" and c2.outline_json["ending_hook"] == "女神现身"
        print("field=beats wrapped object OK")

        print("ALL AGENT TOOLS TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
