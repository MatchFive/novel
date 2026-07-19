"""章节生成 helper 纯函数测试。"""
from __future__ import annotations

from app.agents.harness.workers.chapter_workers import _chapter_summaries_chain


def _ch(order, title, outline="", content=""):
    return {"id": f"id{order}", "order": order, "title": title,
            "detailed_outline": outline, "content": content}


def test_chain_includes_only_previous_chapters():
    chapters = [
        _ch(0, "起", outline="开局设定"),
        _ch(1, "承", outline="主角出山"),
        _ch(2, "转", outline="当前章"),
    ]
    chain = _chapter_summaries_chain(chapters[2], chapters)
    assert "开局设定" in chain
    assert "主角出山" in chain
    assert "当前章" not in chain


def test_chain_falls_back_to_content_and_capped():
    chapters = [_ch(i, f"第{i}章", content="x" * 300) for i in range(20)]
    target = _ch(20, "当前", outline="o")
    chapters.append(target)
    chain = _chapter_summaries_chain(chapters[20], chapters, limit=500)
    assert len(chain) <= 500
    assert chain != "（无）"


def test_chain_empty_when_first_chapter():
    chapters = [_ch(0, "唯一章", outline="大纲")]
    assert _chapter_summaries_chain(chapters[0], chapters) == "（无）"
