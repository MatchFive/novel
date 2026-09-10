"""chapter 域 beats/content 字段入库测试（不调用 LLM）。运行：python tests/test_chapter_fields.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.chapter_service import ChapterService  # noqa: E402
from app.services.command_service import CommandService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        db = Database(Path(tempfile.mkdtemp()) / "cf.db")
        db.init_schema()
        seed_defaults(db)
        project = ProjectService(db).create(name="cf", genre="玄幻", logline="故事")
        svc = CommandService(db, None)
        chs = ChapterService(db)
        chs.create(project_id=project.id, title="落脚", objective="抵达荒原")

        # ---- field=beats：after 为 JSON 数组字符串（含围栏） ----
        beats_json = '```json\n[{"pov": "刘修", "location": "荒原", "event": "抵达并勘察"}, {"event": "搭建第一座棚屋"}]\n```'
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "第1章",
             "field": "beats", "after": beats_json}]}, {0})
        assert r["applied"] == 1, r
        ch = chs.list(project.id)[0]
        assert len(ch.beats_json) == 2 and ch.beats_json[0]["event"] == "抵达并勘察"
        assert ch.beats_json[1]["pov"] == "" and ch.outline_status == "done"
        print("chapter field=beats (fenced json) OK")

        # ---- field=beats：change.beats 直接给列表 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "落脚",
             "field": "beats", "beats": ["事件一", {"event": "事件二", "location": "河边"}]}]}, {0})
        assert r["applied"] == 1, r
        ch = chs.list(project.id)[0]
        assert [b["event"] for b in ch.beats_json] == ["事件一", "事件二"]
        print("chapter field=beats (list) OK")

        # ---- field=beats：非法 JSON 跳过并给原因 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "落脚",
             "field": "beats", "after": "不是JSON"}]}, {0})
        assert r["applied"] == 0 and "无法解析" in r["skipped"][0], r
        print("beats invalid skip OK")

        # ---- field=content：写入正文并统计字数/状态 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "第1章",
             "field": "content", "after": "刘修睁开眼，看见陌生的天空。"}]}, {0})
        assert r["applied"] == 1, r
        ch = chs.list(project.id)[0]
        assert ch.content.startswith("刘修") and ch.word_count > 0 and ch.content_status == "draft"
        print("chapter field=content OK")

        # ---- beat 域：空细纲章节也可 add 追加节拍 ----
        chs.create(project_id=project.id, title="第一桶金", objective="交易")
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "beat", "action": "add", "target": "第一桶金", "after": "与商队讨价还价"}]}, {0})
        assert r["applied"] == 1, r
        ch2 = [c for c in chs.list(project.id) if c.title == "第一桶金"][0]
        assert ch2.beats_json and ch2.beats_json[0]["event"] == "与商队讨价还价"
        assert ch2.outline_status == "done"
        print("beat add on empty-beats chapter OK")

        # ---- 健壮性：after 为 list 时按细纲处理（不再塞进 objective） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "新城",
             "after": ["节拍一", "节拍二"]}]}, {0})
        assert r["applied"] == 1, r
        ch3 = [c for c in chs.list(project.id) if c.title == "新城"][0]
        assert [b["event"] for b in ch3.beats_json] == ["节拍一", "节拍二"]
        assert (ch3.objective or "") == ""
        # 同名再加一次（list 视为新细纲写入，不污染 objective）
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "新城",
             "after": ["节拍三"]}]}, {0})
        assert r["applied"] == 1 and "已写入细纲" in r["applied_notes"][0], r
        # after 为 dict（非节拍结构）则序列化为文本兜底，不崩溃
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "庄园",
             "after": {"简介": "主角的第一个据点"}}]}, {0})
        assert r["applied"] == 1, r
        ch_dict = [c for c in chs.list(project.id) if c.title == "庄园"][0]
        assert "据点" in (ch_dict.objective or "")
        print("after-as-list/dict coercion OK")

        # ---- field=beats：after 直接是 list 也可写入 ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "新城",
             "field": "beats", "after": [{"event": "直入主题"}, "冲突升级"]}]}, {0})
        assert r["applied"] == 1, r
        ch3 = [c for c in chs.list(project.id) if c.title == "新城"][0]
        assert [b["event"] for b in ch3.beats_json] == ["直入主题", "冲突升级"]
        print("beats after-as-list OK")

        # ---- add 时 after 是节拍 JSON：新章节带细纲创建；已存在则写细纲而非污染 objective ----
        beats_arr = '[{"pov": "刘修", "location": "河边", "event": "勘察水源"}, {"event": "搭建水车"}]'
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "水车", "after": beats_arr}]}, {0})
        assert r["applied"] == 1 and "节拍" in r["applied_notes"][0], r
        ch4 = [c for c in chs.list(project.id) if c.title == "水车"][0]
        assert len(ch4.beats_json) == 2 and (ch4.objective or "") == ""
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "add", "target": "水车",
             "after": '[{"event": "新节拍三"}]'}]}, {0})
        assert r["applied"] == 1 and "已写入细纲" in r["applied_notes"][0], r
        ch4 = [c for c in chs.list(project.id) if c.title == "水车"][0]
        assert [b["event"] for b in ch4.beats_json] == ["新节拍三"]
        assert (ch4.objective or "") == "", "细纲 JSON 不应混入 objective"
        print("add with beats payload OK")

        # ---- field=seq：重排序号（用户说 1 起始，存储 0 起始） ----
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "落脚",
             "field": "seq", "after": "3"}]}, {0})
        assert r["applied"] == 1, r
        ch_seq = [c for c in chs.list(project.id) if c.title == "落脚"][0]
        assert ch_seq.seq == 2, ch_seq.seq
        r = svc.apply_plan(project.id, {"changes": [
            {"domain": "chapter", "action": "update", "target": "落脚",
             "field": "seq", "after": "abc"}]}, {0})
        assert r["applied"] == 0 and "数字" in r["skipped"][0], r
        print("chapter field=seq OK")

        print("ALL CHAPTER FIELD TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
