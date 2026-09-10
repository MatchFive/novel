"""编排器历史携带预算测试（不调用 LLM）。运行：python tests/test_orch_history.py"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.workflows.agent_orchestrator import Orchestrator  # noqa: E402


def main() -> int:
    try:
        # 长正文消息应保留前 3000 字（旧版只留 300 字，导致"找不到正文"）
        long_prose = "正文开始。" + "甲" * 5000
        hist = [
            {"role": "user", "content": "帮我写第一章"},
            {"role": "assistant", "content": long_prose},
            {"role": "user", "content": "把前三章的细纲和正文入库"},
        ]
        text = Orchestrator._history_text(hist)
        assert "正文开始。" in text
        assert text.count("甲") == Orchestrator._HISTORY_MSG_CAP - 5, (
            f"长消息应保留单条上限: {text.count('甲')}")
        assert "把前三章的细纲和正文入库" in text
        print("long message cap OK")

        # 总预算：很多条消息时丢弃更早的，保留最新的
        many = [{"role": "user" if i % 2 else "assistant", "content": f"第{i}条" + "乙" * 2000}
                for i in range(20)]
        text2 = Orchestrator._history_text(many)
        assert "第19条" in text2, "最新的消息必须在"
        assert "第0条" not in text2, "超出总预算的旧消息应被丢弃"
        assert len(text2) <= Orchestrator._HISTORY_TOTAL_CAP + 200, len(text2)
        print("total budget OK")

        # 空内容与空历史
        assert Orchestrator._history_text([]) == ""
        t3 = Orchestrator._history_text([{"role": "user", "content": "  "},
                                         {"role": "user", "content": "有效"}])
        assert "有效" in t3 and t3.count("我：") == 1
        print("empty history/content OK")

        print("ALL ORCH HISTORY TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
