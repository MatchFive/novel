"""截断 JSON 修复器测试（不调用 LLM）。运行：python tests/test_json_repair.py"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm_service import _recover_json, _repair_truncated_json  # noqa: E402


def main() -> int:
    try:
        # 场景1：changes 数组第 2 条中间被截断（日志里的真实案例）
        raw = ('{"changes": [{"domain": "chapter", "action": "add", "target": "这波不亏", '
               '"field": "beats", "after": "[{\\"event\\": \\"查分\\"}]"}, '
               '{"domain": "chapter", "action": "add", "target": "过气女神", "field": "be')
        d = _repair_truncated_json(raw)
        assert d and len(d["changes"]) == 1 and d["changes"][0]["target"] == "这波不亏", d
        print("repair mid-item truncation OK")

        # 场景2：字符串值中间被截断
        raw2 = '{"clarification": "", "tasks": [{"entity": "world", "description": "更新晨曦大陆人文社会概览，补充社会'
        d2 = _repair_truncated_json(raw2)
        assert d2 is not None and "tasks" in d2, d2
        print("repair mid-string truncation OK")

        # 场景3：未截断的正常 JSON 原样通过
        d3 = _repair_truncated_json('{"a": 1, "b": [1, 2]}')
        assert d3 == {"a": 1, "b": [1, 2]}
        print("intact json OK")

        # 场景4：完全不是 JSON → None
        assert _repair_truncated_json("hello world") is None
        assert _repair_truncated_json("") is None
        print("non-json OK")

        # 场景5：恢复链——修复优先，抢救兜底
        assert _recover_json(None) is None
        d5 = _recover_json('{"points": [{"title": "a", "content": "x"}, {"title": "b", "con')
        assert d5 and len(d5.get("points", [])) >= 1, d5
        print("recover chain OK")

        # 场景6：含转义引号的字符串截断
        raw6 = '{"changes": [{"target": "第三章", "after": "他说\\"你好\\"，然后"}'
        d6 = _repair_truncated_json(raw6)
        assert d6 and d6["changes"][0]["target"] == "第三章", d6
        print("escaped quote OK")

        print("ALL JSON REPAIR TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
