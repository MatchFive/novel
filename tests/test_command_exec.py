"""指令执行修复测试：新增/删除/模糊匹配/跳过原因（不调用 LLM）。运行：python tests/test_command_exec.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import load_config  # noqa: E402
from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.character_service import CharacterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.outline_service import OutlineService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.world_service import WorldService  # noqa: E402


def main() -> int:
    try:
        cfg = load_config()
        db = Database(Path(tempfile.mkdtemp()) / "cmd.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="cmd", genre="玄幻", logline="故事")
        ws = WorldService(db)
        ws.create_entry(project_id=project.id, category_id=None, title="星火城规则",
                        content="灵气修炼需共鸣。", importance=4)
        cs = CharacterService(db)
        cs.create(project_id=project.id, name="林晚", role_type="protagonist")
        arc = OutlineService(db).create(project_id=project.id, title="第一卷")
        chs = ChapterService(db)
        chs.create(project_id=project.id, arc_id=arc.id, title="初入星火城", objective="进城")
        chs.create(project_id=project.id, arc_id=arc.id, title="夜谈", objective="密谋")
        chs.create(project_id=project.id, arc_id=arc.id, title="觉醒", objective="觉醒血脉")

        svc = CommandService(db, None)

        # ---- 新增（add） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "action": "add", "target": "技术推演规则",
             "after": "推演技术需要时间，世界差距越大耗时越长。"}]}, {0})
        assert r["applied"] == 1 and not r["skipped"], r
        assert any(e.title == "技术推演规则" for e in ws.list_entries(project.id, None))
        print("add world OK")

        # ---- 模糊匹配更新（target 不精确："星火城" 命中"星火城规则"） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "action": "update", "target": "星火城",
             "after": "灵气修炼需共鸣与推演。"}]}, {0})
        assert r["applied"] == 1, r
        e = [x for x in ws.list_entries(project.id, None) if x.title == "星火城规则"][0]
        assert "推演" in e.content
        print("fuzzy match world OK")

        # ---- 章节按"第3章"模糊匹配 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "第3章",
             "field": "objective", "after": "觉醒上古血脉"}]}, {0})
        assert r["applied"] == 1, r
        ch3 = chs.list(project.id)[2]
        assert ch3.objective == "觉醒上古血脉"
        print("chapter 第N章 match OK")

        # ---- 角色新增 + 更新 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "character", "action": "add", "target": "苏晴",
             "profile": {"personality": "聪慧机敏"}},  # 结构化 profile（分字段）
            {"domain": "character", "action": "update", "target": "林晚",
             "field": "profile.personality", "after": "外冷内热、重情义"},
        ]}, {0, 1})
        assert r["applied"] == 2, r
        su = [c for c in cs.list(project.id) if c.name == "苏晴"][0]
        assert su.profile["personality"] == "聪慧机敏"  # 分字段：进 personality 而非 background
        lin = [c for c in cs.list(project.id) if c.name == "林晚"][0]
        assert "重情义" in lin.profile["personality"]
        print("character add/update OK (structured profile)")

        # ---- 删除 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "action": "delete", "target": "技术推演规则"}]}, {0})
        assert r["applied"] == 1, r
        assert not any(e.title == "技术推演规则" for e in ws.list_entries(project.id, None))
        print("delete world OK")

        # ---- 跳过原因（找不到实体时给具体提示） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "action": "update", "target": "不存在的条目", "after": "x"}]}, {0})
        assert r["applied"] == 0 and r["skipped"]
        assert "未找到设定条目" in r["skipped"][0]
        print("skip reason OK:", r["skipped"][0])

        # ---- 新增设定自动归入"其他"分类（视图可见） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "world", "action": "add", "target": "力量体系",
             "after": "斗气/魔法/神眷，九升十需凝聚信仰之力为权柄。"}]}, {0})
        assert r["applied"] == 1, r
        entry = [e for e in ws.list_entries(project.id, None) if e.title == "力量体系"][0]
        assert entry.category_id is not None, "新增设定应归入分类"
        from app.db.repositories.world_repo import WorldCategoryRepository
        with db.session_ctx() as s:
            cats = {c.id: c.name for c in WorldCategoryRepository(s).list_by_project(project.id)}
        assert cats.get(entry.category_id) == "其他", cats.get(entry.category_id)
        print("add world auto-category OK (其他)")

        # ---- 编排器对话历史（_history_text） ----
        from app.workflows.agent_orchestrator import Orchestrator
        hist = [{"role": "user", "content": "主角是刘修，穿越者"},
                {"role": "assistant", "content": "好的，记住了"}]
        text = Orchestrator._history_text(hist)
        assert "刘修" in text and "我：" in text
        print("orchestrator history_text OK")

        print("ALL COMMAND EXEC TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
