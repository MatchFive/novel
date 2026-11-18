"""独立审核（2026-11-15）**第三批**：D5（R1 守卫位的文档降级）与 L2（写作链半成品卡死）。

- **L2**：写作链原先**第一次落盘就写 status="done"**，而 ⑤ 修正 / ⑥ 去味 还没跑 →
  中途崩溃留下**半成品**却标着 done，而幂等门是「status=done 的章不重演」→
  **这一章永远不会被再处理**。→ 中间态改 "writing"，全流程跑完才置 "done"。
- **D5**：审计 R1（数值单调）**没有数据源**（`state.md` 每角色每字段只存最新值，
  历史在投影后被丢弃）→ 文档已降级为「三条规则 + 一条守卫位」；本文件钉住
  「R1 的输入确实只有一个点」这个事实（免得有人以为它还在跑）。
"""
from __future__ import annotations

import json

from harness.projects import create_project
from harness.store.project import ChapterDoc, ProjectStore

# ------------------------------------------------------------------ L2

def test_l2_done_chapters_excludes_writing(tmp_path):
    """**L2**：``done_chapters`` 只认 status=="done" → 半成品**不会**被验收抽样当定稿章。"""
    from harness.check.context import done_chapters

    info = create_project(tmp_path, "L2 抽样书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "定稿章",
                                                "status": "done"}, body="正文一"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "半成品章",
                                                "status": "writing"}, body="正文二"))

    done = done_chapters(store)
    assert [d.seq for d in done] == [1]


# ------------------------------------------------------------------ D5

def test_d5_state_snapshot_keeps_only_latest_value(tmp_path):
    """**D5**：`state.md` 每角色每字段**只存最新值** → R1 的「相邻两次更新」拿不到。

    这条测试钉住**病根**（不是钉住 R1 有没有产出）：若将来有人给章记忆加了
    `state_updates` 第 5 层、让 R1 复活，这条测试会提示「数据源变了、R1 可以复活了」。
    """
    from harness.memory.store import MemoryStore

    mem = MemoryStore(tmp_path)
    mem.apply_state_updates({"characters": [
        {"name": "甲", "level": "100", "updated": 3},
    ]}, 3)
    mem.apply_state_updates({"characters": [
        {"name": "甲", "level": "80", "updated": 5},
    ]}, 5)

    state = mem.read_state()
    entry = state["characters"]["甲"]
    # **只有一个值**（80），没有「100 → 80」的历史
    assert entry["level"] == "80"
    assert "100" not in json.dumps(entry, ensure_ascii=False)
