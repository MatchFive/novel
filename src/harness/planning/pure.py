"""规划链纯函数层（P2 Task 1）。

语义逐行移植自 web_dream（d:/project/web_dream/backend/app/，路径与行号见
task-1-brief.md）：outline_service / project_service / autopilot_runner 中的纯函数
去掉 DB/SQLAlchemy 耦合后收口于此，供后续任务拼提示词与工具。**全部纯函数，无 I/O**；
`strip_chapter_prefix`/`strip_chapter_range` P0 已在 harness.text 逐字复制，直接复用。
"""
from __future__ import annotations

import re

from harness.text import as_text, strip_chapter_prefix, strip_chapter_range

__all__ = [
    "BEATS_MIN_CAP",
    "BEATS_MIN_FLOOR",
    "BEATS_MIN_PER_WORDS",
    "DEFAULT_TARGET_WORDS",
    "DEFAULT_TOTAL_CHAPTERS",
    "GROUP_OVER_FACTOR",
    "REPEAT_OVERLAP",
    "TAIL_FACTOR",
    "TAIL_MERGE_FACTOR",
    "UNITS_MIN_CAP",
    "UNITS_MIN_FLOOR",
    "UNIT_WORDS",
    "anchor_chars",
    "beats_thin_note",
    "character_digest",
    "clean_beats",
    "clean_units",
    "content_repeat",
    "derive_objective",
    "derive_title",
    "group_anchors",
    "min_beats",
    "min_units",
    "scale_block",
    "slim_unit",
    "story_block",
    "strip_chapter_prefix",
    "strip_chapter_range",
    "units_thin_note",
    "units_to_chapters",
]

# ================================================================ 拆章第一步：剧情描述
# 「情节 →（剧情描述）→ 章节」：先把情节展开成一串**粗粒度事件段**（一段 ≈ 1500~2500 字正文，
# 带时间/地点/预估字数），章再由代码按**章字数目标**把若干段**归组**推出（多段可并进同一章）。
# 目的：章数由**内容量**决定——内容撑不满预期章数就得到更少的章（每章饱满），而不是硬切成多章、
# 每章单薄（真实事故：目标 4000 字的章只写出 1495 字，细纲本身就撑不起）。
UNIT_WORDS = 1800          # 一段剧情描述约写多少字正文（取 1500~2500 折中）
UNITS_MIN_FLOOR = 3        # 至少几段（保证「多段并进同一章」这条机制真的会被用到）
UNITS_MIN_CAP = 12         # 上限（再多就没必要，属于该分卷了）

# `with_shrink` 的 `valid` 只保证「至少 1 拍」，模型常把整章压成 2 拍 →
# 正文只写得出千余字（真实事故：项目 7 第 3 章 2 拍 → 1495 字，目标 4000）。故「太少」也要补足一轮。
BEATS_MIN_PER_WORDS = 700
BEATS_MIN_FLOOR = 4
BEATS_MIN_CAP = 10

# 归组切章：闭组阈值（累计到 1.2×目标先收）、尾巴（<0.4×目标）并尾（≤1.6×目标）——治「单薄尾巴章」。
GROUP_OVER_FACTOR = 1.2
TAIL_FACTOR = 0.4
TAIL_MERGE_FACTOR = 1.6
# 相邻章 covers 字集重叠阈值（达到即视为在写同一件事）。
REPEAT_OVERLAP = 0.5

DEFAULT_TARGET_WORDS = 3000
DEFAULT_TOTAL_CHAPTERS = 100


def min_units(target: int) -> int:
    """一个情节**至少**该拆出多少段剧情描述。

    口径：一个情节通常要撑起 **2 章左右**，一段约 `UNIT_WORDS` 字 ⇒ `ceil(2×章目标 ÷ 1800)`，
    再夹在 `[UNITS_MIN_FLOOR, UNITS_MIN_CAP]`。低于此数就是「覆盖面不够」，附「补足」附注再产一轮
    （与 `min_beats` 同一类兜底：**只有真变多才采用**）。
    """
    try:
        words = int(target)
    except (TypeError, ValueError):
        words = 4000
    need = -(-(2 * max(1, words)) // UNIT_WORDS)          # ceil(2*words / 1800)
    return max(UNITS_MIN_FLOOR, min(UNITS_MIN_CAP, need))


def min_beats(target: int) -> int:
    """目标字数对应的**最少**节拍数（限 [4, 10]，与「6~10 拍」口径一致）。"""
    try:
        words = int(target)
    except (TypeError, ValueError):
        words = 4000
    need = -(-max(1, words) // BEATS_MIN_PER_WORDS)          # ceil(words / 700)
    return max(BEATS_MIN_FLOOR, min(BEATS_MIN_CAP, need))


def units_thin_note(got: int, need: int) -> str:
    """剧情描述偏少时的**补足**附注（方向与收缩重试相反：要更多、更细）。"""
    return (f"\n【剧情描述太少·补足】上一次只给了 {got} 段，覆盖不了本情节的容量"
            f"（一段约 1500~2500 字，本情节至少需要 {need} 段）。请在**不改变情节边界、"
            f"不新增其它情节内容**的前提下，把本情节展开得更细：一次对话回合、一次场景推进、"
            f"一次行动与结果都可以单独成段；补足到 **{need}~{need + 4} 段**，"
            f"每段仍只写「谁/在哪/对谁/做了什么/结果」，并给全 time/location/expected_words/covers。")


def beats_thin_note(got: int, need: int) -> str:
    """节拍偏少时的**补足**附注（方向与收缩重试相反：要更多、更细，而不是更少）。"""
    return (f"\n【节拍太少·补足】上一次只给了 {got} 个节拍，撑不起本章的目标字数"
            f"（一个节拍正文约 500~800 字，至少要 {need} 个）。请在**不改变本章事件边界、"
            f"不新增别章内容**的前提下，把这一章拆得更细：一次对话回合、一个动作细节、"
            f"一次场景推进、一处身体反应都可以单独成拍；补足到 **{need}~{need + 4} 个饱满节拍**，"
            f"每个 event 仍只写「谁/在哪/对谁/做了什么/结果」。仍然只写本章、不得越界替别的章写。")


def clean_units(raw: object) -> list[dict]:
    """剧情描述的清洗入口：非 dict / 空 event 判无效（→ 收缩重试）；顺带把 `expected_words` 数字化。

    与 `clean_beats` 同款——模型给出的「看似有、实则空」的条目不能落库，更不能拿它去切章。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for u in raw:
        if not isinstance(u, dict) or not str(u.get("event") or "").strip():
            continue
        u = dict(u)
        try:
            words = int(float(u.get("expected_words")))
        except (TypeError, ValueError):
            words = 0
        u["expected_words"] = words if words > 0 else UNIT_WORDS
        out.append(u)
    return out


def clean_beats(raw: object) -> list[dict]:
    """拆章节拍的唯一清洗入口：模型对节拍常返回 `["拍一", …]` 或 `[{"event": ""}]`——
    这些进了库会被下游过滤成空，造成「看似有细纲、实则无节拍」。
    非 dict / 空 event 一律判为无效（→ 收缩重试）。
    """
    if not isinstance(raw, list):
        return []
    return [b for b in raw
            if isinstance(b, dict) and str(b.get("event") or "").strip()]


def _unit_words(u: dict) -> int:
    try:
        w = int(u.get("expected_words") or UNIT_WORDS)
    except (TypeError, ValueError):
        w = UNIT_WORDS
    return w if w > 0 else UNIT_WORDS


def units_to_chapters(units: list[dict], target_words: int) -> list[list[dict]]:
    """按**章字数目标**把剧情描述归组为章——**边界由代码定**，且**绝不切断某一段**。

    规则（都在段边界上切，所以切点天然落在场景/事件边界）：
    - 累计预估字数到达章目标 ⇒ 收一章；
    - **单段预估就超过章目标** ⇒ 该段独占一章（内容摆在那，不硬拆，允许该章略长）；
    - 末尾剩余段合计 < 章目标的 40%、且并入上一章不超 1.6× 目标 ⇒ **并入上一章**，
      避免出现一个「单薄尾巴章」——这正是本方案要治的「单章写不满」。
    """
    if not units:
        return []
    limit = max(1, int(target_words))
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_w = 0
    for u in units:
        w = _unit_words(u)
        if cur and cur_w + w > limit * GROUP_OVER_FACTOR:      # 再加这一段就明显超了：先收一章
            groups.append(cur)
            cur, cur_w = [], 0
        cur.append(u)
        cur_w += w
        if cur_w >= limit:                        # 到目标（含「单段即超目标」）→ 收
            groups.append(cur)
            cur, cur_w = [], 0
    if cur:
        groups.append(cur)
    if len(groups) >= 2:
        tail_w = sum(_unit_words(u) for u in groups[-1])
        prev_w = sum(_unit_words(u) for u in groups[-2])
        if tail_w < limit * TAIL_FACTOR and prev_w + tail_w <= limit * TAIL_MERGE_FACTOR:
            groups[-2] = groups[-2] + groups[-1]
            groups.pop()
    return groups


def slim_unit(u: dict) -> dict:
    """落库用的剧情描述段：只留已知字段（模型可能塞进来路不明的多余键）。"""
    out: dict = {}
    for k in ("seq", "time", "location", "event", "covers", "expected_words"):
        if u.get(k) not in (None, ""):
            out[k] = u.get(k)
    chars = u.get("characters")
    if isinstance(chars, list) and chars:
        out["characters"] = [str(c) for c in chars if str(c).strip()]
    return out


def group_anchors(group: list[dict]) -> dict:
    """把一章所辖的**段**汇总成章级「时空锚点」（covers/跨度/起/止），供并行扩拍与去重复用。"""
    covers = "；".join(str(u.get("covers") or u.get("event") or "").strip()
                       for u in group if (u.get("covers") or u.get("event")))
    first, last = group[0], group[-1]

    def _at(u: dict) -> str:
        return "｜".join(x for x in (str(u.get("location") or "").strip(),
                                     str(u.get("time") or "").strip()) if x)

    t0 = str(first.get("time") or "").strip()
    t1 = str(last.get("time") or "").strip()
    time_span = t0 if (t0 and t0 == t1) else (f"{t0} → {t1}" if (t0 and t1) else (t0 or t1))
    return {"covers": covers[:300], "time_span": time_span,
            "start_at": _at(first), "end_at": _at(last)}


def derive_title(group: list[dict]) -> str:
    """补章头失败时的**退化标题**：取首段概括（保证不为空、不带「第N章」）。"""
    t = str(group[0].get("covers") or group[0].get("event") or "").strip()
    return strip_chapter_prefix(t)[:20] or "未命名"


def derive_objective(group: list[dict]) -> str:
    """补章头失败时的**退化目标**：把所辖段的 event 串起来。"""
    return "；".join(str(u.get("event") or "").strip() for u in group)[:200]


def anchor_chars(text: str) -> set[str]:
    """文本的字符集（去掉标点/空白/换行）——用于粗判两处内容是否在讲同一件事。"""
    return set(re.sub(r"[\s，。、；：,.;:｜|/·—\-（）()「」『』“”]+", "", text or ""))


def content_repeat(prev_covers: str, cur_covers: str) -> bool:
    """相邻两章是否在写**同一件事**（`covers` 字集重叠 ≥ `REPEAT_OVERLAP`）。

    网文里**时间/地点跳跃是正常的**，所以这里**不判"换场有没有交代"**；真正要拦的是**重复**：
    上一章已经写完的阶段，下一章又写一遍。**只做提示**，**不阻断**任何流程。
    """
    a, b = anchor_chars(prev_covers), anchor_chars(cur_covers)
    if not a or not b:
        return False
    if a.issubset(b) or b.issubset(a):
        return True                      # 一方包含另一方（「出关」vs「出关后赶到青石城」）也算重复
    return len(a & b) / min(len(a), len(b)) >= REPEAT_OVERLAP


def character_digest(cards: list[dict], bg_limit: int = 260) -> str:
    """既有角色摘要：`名字(定位｜别名：a、b)：background 摘要`。

    只给名字时，模型看不到主角已存在，就会「重新发明」一个新主角；**别名也必须给**，否则
    模型会反复产出同名卡片。**背景摘要必须给足**（情节规划调大到 260 字）——背景太短看不到
    内容，模型就会给角色现编与之矛盾的过去/经济状况。
    """
    if not cards:
        return "（无）"
    lines = []
    for c in cards:
        prof = c.get("profile") if isinstance(c.get("profile"), dict) else {}
        one = str(as_text(prof.get("background") or prof.get("personality")) or "")[:bg_limit]
        tag = str(c.get("role_type") or "角色")
        aliases = str(c.get("aliases") or "").strip()
        if aliases:
            tag += f"｜别名：{aliases}"
        lines.append(f"{c.get('name')}({tag})" + (f"：{one}" if one else ""))
    return "\n".join(lines)


def scale_block(target_words: int, total_chapters: int, arc_chapters: int | None = None) -> str:
    """规模口径（固定注入 system prompt）。

    2026-10-09 软化：口径只是**容量参考**（情节按字数容量设计），不再是硬性配额——
    拆章时章数由剧情内容量推出，允许偏离（偏少不补、不注水、不重拆凑数）。
    """
    tw = max(1, int(target_words or DEFAULT_TARGET_WORDS))
    tc = max(1, int(total_chapters or DEFAULT_TOTAL_CHAPTERS))
    lines = [
        "【规模口径（项目级参考规模：用户已设定；**只用于折算容量参考，非硬性配额**）】",
        f"每章约 {tw} 字；全书共 {tc} 章（约 {tw * tc} 字）。",
        (f"凡出现「X 万字」即折算为「X×10000÷{tw} 章」作容量参考；不得改动每章字数。"
         "拆章时章数由剧情内容量推出，与参考值有出入是正常的——**偏少不补、不注水、"
         "不为凑章数重拆**。"),
    ]
    if arc_chapters:
        lines.append(f"本卷容量参考约 {arc_chapters} 章（情节按字数容量设计，非硬性配额）。")
    return "\n" + "\n".join(lines)


def story_block(genre: str, tone: str, ending: str) -> str:
    """题材/基调/结局（项目级创作设定，固定注入 system prompt）。

    三项全未设置时返回空串（不制造噪音）。
    """
    parts: list[str] = []
    genre = str(genre or "").strip()
    tone = str(tone or "").strip()
    ending = str(ending or "").strip()
    if genre:
        parts.append(f"题材：{genre}")
    if tone:
        parts.append(f"基调：{tone}（全书风格与爽点节奏以此为纲——角色性格/情节设计/正文文风"
                     "都必须与之对齐，不得写出与基调相悖的内容）")
    if ending:
        parts.append(f"结局走向：{ending}（情节推进必须朝这个收束方向积累，不得背道而驰）")
    if not parts:
        return ""
    return "\n【创作基调（项目级设定，用户已定，必须遵守）】\n" + "\n".join(parts)
