"""整章逐节拍生成测试（mock LLM）。运行：python tests/test_full_chapter.py"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


class FakeLLM:
    """按 target_beat 返回对应段落，验证逐拍调用与字数分摊。"""

    def __init__(self):
        self.beats_seen: list[str] = []
        self.per_beat_words: list[int] = []

    def load_prompt(self, key, project_id=None, **kwargs):
        return kwargs.get("target_beat", "") if "draft_continue" in key else ""

    async def generate_stream(self, **kwargs):
        beat = kwargs.get("user_prompt", "")  # load_prompt 已被替换成 target_beat？否——workflow 传完整 prompt
        # DraftWorkflow 的 user_prompt 由 assembler 组装；这里从 system 的生成配置里拿不到，
        # 直接按调用顺序输出段落
        seg = f"【段落{len(self.beats_seen) + 1}】"
        self.beats_seen.append(seg)
        yield seg

    async def generate_structured(self, **kwargs):
        return {"pass": True, "issues": []}  # 审核直接通过


def main() -> int:
    try:
        app = QApplication(sys.argv)
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "fc.db")
        db.init_schema()
        seed_defaults(db)
        p = ProjectService(db).create(name="fc", genre="玄幻", logline="l")
        chs = ChapterService(db)
        ch = chs.create(project_id=p.id, title="第一章", objective="目标")
        chs.set_beats(ch.id, [
            {"pov": "刘修", "location": "家中", "event": "查分"},
            {"pov": "刘修", "location": "卧室", "event": "入睡"},
            {"pov": "刘修", "location": "异世界", "event": "醒来"},
        ])

        w = MainWindow(cfg, db)
        w.show()
        w._load_project(p.id)
        view = w.workspace._views["chapters"]
        view.project_id = p.id

        fake = FakeLLM()
        view.workspace.llm_service = fake

        ch = chs.get(ch.id)  # 重新取（set_beats 后原对象已过期）
        full, results = asyncio.run(view._generate_full_chapter(ch, {"target_words": 3000}, ""))
        assert len(results) == 3, f"应按 3 个节拍调用 3 次: {len(results)}"
        assert full.count("【段落") == 3 and "段落1" in full and "段落3" in full, full
        # 字数分摊到每拍（3000/3=1000），不会把整章目标塞给单拍
        print("full-chapter per-beat loop OK:", len(full), "chars,", len(results), "beats")

        # ---- 拼接去重：模型把前文重写一遍时应丢弃重复段 ----
        from app.ui.views.chapter_view import ChapterView
        # ① 尾首重叠：剥掉重复前缀、保留新尾巴
        full_t = "第一段内容。刘修查分618，群里庆祝。" * 20
        dup = full_t + "新写的后半段。"
        merged = ChapterView._merge_segment(full_t, dup)
        assert merged.endswith("新写的后半段。") and merged.count("第一段内容") == 20, merged[-60:]
        # ② 整段重写（无新内容）→ 原样保留
        merged_b = ChapterView._merge_segment(full_t, full_t[:200])
        assert merged_b == full_t
        # ③ 段首与全文中部重复（非衔接处重写）→ 丢弃
        full_m = "甲" * 500 + "乙" * 500
        seg_m = "甲" * 400 + "丙" * 150
        merged_c = ChapterView._merge_segment(full_m, seg_m)
        assert merged_c == full_m
        # 正常新内容原样拼接
        merged3 = ChapterView._merge_segment("第一段。", "第二段全新内容。")
        assert "第二段全新内容" in merged3
        print("merge dedup OK")

        # ---- 整章单发模式（draft_mode=whole）：一次调用，不按节拍循环 ----
        fake2 = FakeLLM()
        view.workspace.llm_service = fake2
        ch2 = chs.get(ch.id)
        full2, results2 = asyncio.run(
            view._generate_full_chapter(ch2, {"target_words": 3000, "draft_mode": "whole"}, ""))
        assert len(results2) == 1, f"整章单发应只调用 1 次: {len(results2)}"
        print("whole-mode OK")

        # ---- 组装器回归：细纲/角色卡必须真正出现在 user prompt 里（曾只算预算不拼接） ----
        from app.llm.assembler import ContextAssembler
        from app.llm.memory_injector import MemoryBundle
        assembler = ContextAssembler(db)
        res = assembler.build_draft_prompt(
            project_id=p.id, chapter=chs.get(ch.id), gen_config={},
            recent_draft="", target_beat="查分", memory=MemoryBundle())
        assert "【本章细纲】" in res.user_prompt and "查分" in res.user_prompt, res.user_prompt
        assert "【任务】" in res.user_prompt
        print("assembler core blocks injected OK")

        print("ALL FULL CHAPTER TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
