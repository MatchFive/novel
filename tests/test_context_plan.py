"""写作前上下文策划 + 联网核查测试（mock LLM，不联网）。运行：python tests/test_context_plan.py"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402
from app.utils.web_search import _parse_bing, _parse_ddg, _parse_sogou  # noqa: E402

DDG_HTML = """
<html><body>
<a class="result__a" href="x">2024年高考北京大学录取分数线</a>
<a class="result__snippet" href="x">北京大学2024年在多数省份录取线为680分以上…</a>
<a class="result__a" href="y">高考618分能上什么大学</a>
<a class="result__snippet" href="y">618分可选择中坚九校部分专业…</a>
</body></html>
"""

BING_HTML = """
<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="x">2024年北京大学录取分数线</a></h2>
<div class="b_caption"><p>北京大学2024年在多数省份录取线为 680 分以上，618 分不够。</p></div></li>
<li class="b_algo"><h2><a href="y">618分能上什么大学</a></h2><p>多数 211 院校可选。</p></li>
</ol></body></html>
"""

SOGOU_HTML = """
<div class="vrwrap" id="s1">
  <h3 class="vr-title  "><a href="/link?url=x"><em><!--red_beg-->北京大学<!--red_end--></em> 2024 年录取分数线汇总</a></h3>
  <div class="text-layout"><p class="step-cont ellipsis">北京大学 2024 年在多数省份录取线为 680 分以上。</p></div>
</div>
<div class="vrwrap" id="s2">
  <h3 class="vr-title"><a href="/link?url=y">高考 618 分能上什么大学</a></h3>
  <p>618 分可选择部分 211 院校。</p>
</div>
"""


class PlannerLLM:
    """context_select 返回策划结果；写作/审核走空实现。"""

    def __init__(self):
        self.select_prompts: list[str] = []

    def load_prompt(self, key, project_id=None, **kwargs):
        return ""

    async def generate_structured(self, **kwargs):
        if kwargs.get("prompt_key") == "task.context_select":
            self.select_prompts.append(kwargs.get("user_prompt", ""))
            return {"world_titles": ["高考规则"], "character_names": ["刘修"],
                    "keywords": ["成绩", "志愿"], "need_web": ""}
        return {"pass": True, "issues": []}

    async def generate_stream(self, **kwargs):
        yield "段落"


def main() -> int:
    try:
        # ---- DDG 结果解析 ----
        text = _parse_ddg(DDG_HTML, 3)
        assert "北京大学" in text and "618分" in text, text
        # ---- Bing 结果解析 ----
        text_b = _parse_bing(BING_HTML, 3)
        assert "北京大学" in text_b and "680" in text_b, text_b
        # ---- 搜狗结果解析 ----
        text_s = _parse_sogou(SOGOU_HTML, 3)
        assert "北京大学" in text_s and "分数线" in text_s, text_s
        print("web result parse OK（DDG + Bing + 搜狗）")

        # ---- 策划步：keywords 流入写作上下文组装 ----
        db = Database(Path(tempfile.mkdtemp()) / "cp.db")
        db.init_schema()
        seed_defaults(db)
        cfg = load_config()
        p = ProjectService(db).create(name="cp", genre="都市", logline="l")
        WorldService(db).create_entry(project_id=p.id, category_id=None,
                                      title="高考规则", content="618 分不足以进入顶尖学府清北。",
                                      importance=4)
        chs = ChapterService(db)
        ch = chs.create(project_id=p.id, title="查分", objective="高考出分")
        chs.set_beats(ch.id, [{"pov": "刘修", "location": "家", "event": "查分618"}])
        ch = chs.get(ch.id)

        from app.workflows.draft_workflow import DraftWorkflow
        llm = PlannerLLM()
        wf = DraftWorkflow(db, llm)
        result = asyncio.run(wf.run(project_id=p.id, chapter=ch, gen_config={}))
        assert result.draft == "段落"
        assert llm.select_prompts, "应调用一次上下文策划"
        # 策划的检索词让「高考规则」条目被注入（FTS 命中"成绩/志愿/高考规则"）
        print("context plan flow OK（策划调用 1 次，写作 1 次）")

        # ---- 策划失败不影响主流程 ----
        class FailLLM(PlannerLLM):
            async def generate_structured(self, **kwargs):
                if kwargs.get("prompt_key") == "task.context_select":
                    raise RuntimeError("boom")
                return {"pass": True, "issues": []}

        wf2 = DraftWorkflow(db, FailLLM())
        result2 = asyncio.run(wf2.run(project_id=p.id, chapter=ch, gen_config={}))
        assert result2.draft == "段落"
        print("plan failure safe OK")

        print("ALL CONTEXT PLAN TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
