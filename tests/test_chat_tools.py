"""聊天助手自主读取工具测试（mock LLM）。运行：python tests/test_chat_tools.py"""
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
from app.services.chat_service import ChatService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.read_tools import extract_read_calls, is_read_only_output  # noqa: E402


class FakeStreamLLM:
    """第一轮输出读取标记，第二轮输出含正文关键字的正式回答。"""

    def __init__(self):
        self.calls = 0

    def load_prompt(self, key, project_id=None, **kwargs):
        return ""  # 基础提示词留空即可

    def resolve_plain(self, project_id, module, prompt_key="chat"):
        return None

    async def stream_raw(self, messages, resolved, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield "[[READ:chapter_content:第1章]]"
        else:
            # 第二轮：messages 里应带着正文（灰石领男爵）
            assert any("灰石领男爵" in (m.get("content") or "") for m in messages), \
                "第二轮应携带读取到的正文"
            for ch in "读完正文了：第一章里刘修成为灰石领男爵":
                yield ch


class PlainLLM(FakeStreamLLM):
    async def stream_raw(self, messages, resolved, **kwargs):
        self.calls += 1
        yield "直接"
        yield "回答"


async def collect(svc, project_id):
    out = ""
    async for chunk in svc.chat_stream(project_id, [], "第一章写了什么"):
        out += chunk
    return out


def main() -> int:
    try:
        db = Database(Path(tempfile.mkdtemp()) / "ct.db")
        db.init_schema()
        seed_defaults(db)
        p = ProjectService(db).create(name="ct", genre="玄幻", logline="l")
        chs = ChapterService(db)
        chs.create(project_id=p.id, title="这波不亏",
                   objective="查分") if False else None
        ch = chs.create(project_id=p.id, title="这波不亏", objective="查分")
        chs.update(ch.id, content="刘修穿越成为灰石领男爵，身边躺着伊维娜。")

        # 标记解析
        calls = extract_read_calls("[[READ:chapter_content:第1章]]")
        assert calls == [("chapter_content", "第1章")], calls
        assert is_read_only_output("[[READ:chapter_content:第1章]]")
        assert not is_read_only_output("正常回答")
        print("marker parse OK")

        # 工具循环：第一轮 READ → 第二轮正式回答且带着正文
        svc = ChatService(db, FakeStreamLLM())
        out = asyncio.run(collect(svc, p.id))
        assert "灰石领男爵" in out, out
        print("chat tool loop OK")

        # 普通回答不触发工具
        svc2 = ChatService(db, PlainLLM())
        out2 = asyncio.run(collect(svc2, p.id))
        assert out2 == "直接回答", out2
        print("plain chat passthrough OK")

        print("ALL CHAT TOOLS TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
