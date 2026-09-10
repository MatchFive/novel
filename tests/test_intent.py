"""修改意图识别测试（不调用 LLM）。运行：python tests/test_intent.py"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.chat_service import is_modification_intent  # noqa: E402

CASES_TRUE = [
    "更新前三章的内容",
    "章节和细纲也要入库啊",
    "把第3章的目标改成突出冲突",
    "根据第一卷大纲生成对应章节的细纲",
    "把这段正文写入第1章",
    "新增一个角色卡",
    "给我生成前三章",
    "删除设定「力量体系」",
]

CASES_FALSE = [
    "我有个点子，想聊聊主角的性格",
    "你觉得这个伏笔怎么样？",
    "帮我看看这段话通不通顺",
    "今天天气不错",
]


def main() -> int:
    try:
        for text in CASES_TRUE:
            assert is_modification_intent(text), f"应识别为修改意图: {text}"
        for text in CASES_FALSE:
            assert not is_modification_intent(text), f"不应识别为修改意图: {text}"
        print(f"intent OK: {len(CASES_TRUE)} true + {len(CASES_FALSE)} false cases")
        print("ALL INTENT TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
