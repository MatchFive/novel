"""临时验证：截断 JSON 抢救逻辑。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm_service import _salvage_json  # noqa: E402

# 场景1：points 数组在第 3 条中间被截断（模拟用户遇到的 Unterminated string）
raw = ('{"points": [{"title": "黄金三章-冲突前置", "category": "大纲", "content": "第一章300字内抛出核心冲突。"}, '
       '{"title": "配角-功能性定位", "category": "角色", "content": "配角承担推动剧情或映照主角的功能。"}, '
       '{"title": "反派动机", "category": "角色", "content": "反派要有自洽')
d = _salvage_json(raw)
assert d and len(d["points"]) == 2, d
print("salvage truncated OK:", [p["title"] for p in d["points"]])

# 场景2：单个完整对象原样返回
d2 = _salvage_json('{"a": 1, "b": {"c": 2}}')
assert d2 == {"a": 1, "b": {"c": 2}}
print("single object OK")

# 场景3：无法抢救返回 None
assert _salvage_json("not json at all") is None
assert _salvage_json("") is None
print("no-salvage OK")

# 场景4：代码围栏 + 截断
raw4 = '```json\n{"points": [{"title": "t", "category": "正文", "content": "c"}], "note": "截'
d4 = _salvage_json(raw4)
assert d4 and d4["points"][0]["title"] == "t", d4
print("fenced truncated OK")

# 场景5：抢救结果能被 handbook 的 _normalize_points 正确消化
from app.services.handbook_service import HandbookService  # noqa: E402
pts = HandbookService._normalize_points(d["points"])
assert len(pts) == 2 and pts[0][1] == "大纲" and pts[1][1] == "角色", pts
print("normalize OK:", pts[0])

print("ALL SALVAGE TESTS PASSED")
