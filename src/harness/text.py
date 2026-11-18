# 来源：d:/project/web_dream/backend/app/core/text.py（2026-11-13 逐字复制，seed copy 不回源）
"""文本清洗与小工具：章节标题规整、「梗/名场面」设计渲染、LLM 返回值类型规整。"""
from __future__ import annotations

import json
import re

# 「第14章」「第十二回」「第 3 节」等前缀（N 为阿拉伯数字或中文数字）
_CHAPTER_PREFIX = re.compile(
    r"^\s*第\s*[0-9零一二三四五六七八九十百千两]+\s*[章回节]\s*[：:、.．·\-—]?\s*")
# 整体被引号/书名号包裹的标题：剥掉编号后多半会留下孤零零的右括号，一并去掉外层
_WRAPPED = re.compile(r"^[「『\"'（(]\s*(?P<inner>.+?)\s*[」』\"'）)]$")
# 章号区间后缀：「（第1—12章）」「(第13-25章)」「（第48章起）」等（全半角括号、
# 阿拉伯/中文数字、—/-/~/– 连接）。2026-10-09 实跑：模型把固定章号区间写进卷/情节
# 标题 → 拆章（按内容量推章数）对不上 → 员工误判缺陷反复 resplit 凑数。
_CHAPTER_RANGE_SUFFIX = re.compile(
    r"[（(]\s*第\s*[0-9零一二三四五六七八九十百千两]+\s*"
    r"[—–\-~～]\s*[0-9零一二三四五六七八九十百千两]+\s*章\s*[）)]"
    r"|\s*[（(]\s*第\s*[0-9零一二三四五六七八九十百千两]+\s*章\s*起\s*[）)]")

# 章节标题的合理长度上限：超过基本可断定「把细纲/正文当标题写进来了」
TITLE_MAX_LEN = 60


def strip_chapter_prefix(title: str | None) -> str | None:
    """去掉标题开头的「第N章」编号（含剥掉编号后残留的「」""包裹）。

    编号逐章增删会变，写死进标题必然对不上，所以入库前一律剥离：
    「第14章「献祭权柄」」→「献祭权柄」；剥空则返回空串（由 seq 兜底显示）。
    没有编号前缀时原样返回——标题自带的引号/书名号不动。
    """
    if not title:
        return title
    cleaned, stripped = _CHAPTER_PREFIX.subn("", title)
    if not stripped:
        return title
    cleaned = cleaned.strip()
    wrapped = _WRAPPED.match(cleaned)
    if wrapped:
        cleaned = wrapped.group("inner").strip()
    return cleaned


def strip_chapter_range(title: str | None) -> str | None:
    """去掉标题末尾的「（第N—M章）」类章号区间标注（可多次出现）。

    章号区间是**规划阶段的临时估算**，拆章时章数由剧情内容量推出、必然对不上；
    写进标题会把「对不上」渲染成「缺陷」，诱导员工为重凑章数反复重拆。入库前一律剥掉：
    「冒名的皮（第1—12章）」→「冒名的皮」；「江穗（第48章起）」→「江穗」。
    没有区间标注时原样返回。
    """
    if not title:
        return title
    cleaned, stripped = _CHAPTER_RANGE_SUFFIX.subn("", title)
    return cleaned.strip() if stripped else title


def looks_like_title(value: str | None) -> bool:
    """这段文本像不像一个章节标题。

    用来挡掉 LLM 把**细纲 JSON / 整章正文**写进 `title` 的误操作（真实事故：`edit_by_command`
    的 chapter 写入没有校验，四章的标题分别变成 1512~4922 字的正文或 `{"scene":…}` 细纲 JSON，
    章节列表直接没法看）。标题应当短、单行、不是 JSON。
    """
    text = (value or "").strip()
    if not text or len(text) > TITLE_MAX_LEN:
        return False
    if "\n" in text or "\r" in text:
        return False
    return not text.startswith(("{", "[", "```"))


# 折叠时统一掉的「同义标点/引号」：模型抄引文时排版常与正文略有出入
_QUOTE_MAP = {
    "“": "\"", "”": "\"", "「": "\"", "」": "\"", "『": "\"", "』": "\"",
    "‘": "'", "’": "'", "，": ",", "。": ".", "、": ",", "；": ";",
    "：": ":", "！": "!", "？": "?", "（": "(", "）": ")", "—": "-", "…": ".",
}


def _fold(text: str) -> tuple[str, list[int]]:
    """折叠成可比形态，并给出「折叠后下标 → 原串下标」的映射（用于还原偏移）。"""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        chars.append(_QUOTE_MAP.get(ch, ch))
        index.append(i)
    return "".join(chars), index


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """在正文里定位一段引文，返回 (start, end)；定位不到返回 None。

    两级匹配：① 精确；② **折叠**匹配（去空白 + 统一引号/标点）。**不做模糊匹配**——
    宁可不改，也绝不拿猜出来的偏移去动正文（有问题的引文会当成未处理问题上报）。
    """
    quote = (quote or "").strip()
    if not text or not quote:
        return None
    pos = text.find(quote)
    if pos >= 0:
        return pos, pos + len(quote)
    folded_text, index = _fold(text)
    folded_quote, _ = _fold(quote)
    if not folded_quote:
        return None
    pos = folded_text.find(folded_quote)
    if pos < 0:
        return None
    return index[pos], index[pos + len(folded_quote) - 1] + 1


def as_text(value, sep: str = "\n") -> str | None:
    """把 LLM 返回值压成可写入文本列的字符串。

    模型对「关键台词」「获得/失去」这类语义上是复数的字段经常返回数组，而对应列是
    Text/String——SQLite 会直接报 `Error binding parameter: type 'list' is not supported`
    （PostgreSQL 也不会自动适配），整章记忆提取随之失败。数组按行拼接、对象转 JSON，
    保证写库前一定拿到字符串。
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = [as_text(v, sep) for v in value]
        return sep.join(p for p in parts if p)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def as_list(value) -> list:
    """把 LLM 返回值规整成列表（字符串按单元素处理，避免被逐字符迭代）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# 角色卡 profile 的**规范键**：界面（`Characters.tsx`）与正文注入（`assembler._retrieve_cast`）
# 只认这六个英文键。
PROFILE_FIELDS = ("appearance", "personality", "background", "motivation", "ability", "speech_style")
# 中文键名 → 规范英文键（模型常按需求里的中文标签多输出「性格/说话风格」等键）
_PROFILE_KEY_ALIASES = {
    "外貌": "appearance", "性格": "personality", "背景": "background",
    "动机": "motivation", "能力": "ability", "说话风格": "speech_style",
}


def normalize_profile(profile: dict | None) -> dict:
    """把角色卡 profile 归一到六个规范英文键：中文键名折进去，未识别的键丢弃。

    为什么必须收口：模型会照着需求里的中文标签多输出「性格/说话风格」等键——而界面与正文注入
    **只读**这六个英文键，落在中文键里的关键人设**没有任何消费方，等于没写**（真实事故：陆昭卡
    把「自信张扬」写在 `性格` 键里，`personality` 只剩一堆毛病，卡片读起来「改了跟没改一样」）。
    同名键冲突时不丢信息：内容互相包含时取更全的那份，否则换行拼接。
    """
    out: dict[str, str] = {}
    for raw_key, raw_val in (profile or {}).items():
        key = _PROFILE_KEY_ALIASES.get(str(raw_key).strip(), str(raw_key).strip())
        if key not in PROFILE_FIELDS:
            continue
        val = (as_text(raw_val) or "").strip()
        if not val:
            continue
        prev = out.get(key, "")
        if not prev:
            out[key] = val
        elif val in prev:
            continue
        elif prev in val:
            out[key] = val
        else:
            out[key] = f"{prev}\n{val}"
    return out


def merge_revised_profile(original: dict | None, revised: dict | None) -> dict:
    """把「定点修订」的输出**逐字段**合并回原版 profile（2026-10-11 事故修复）。

    背景：`task.auto_character_revise` 要求模型输出完整卡、未点名字段原样返回——实测模型
    常把**改写内容写进中文键**（验收意见用「性格」等中文措辞，模型照写），同时把旧内容
    原样留在英文键。若走 `normalize_profile` 的同名兜底（互不包含则换行拼接），落库的
    personality = 旧性格 + 新性格并排 ⇒ 一张卡里两套割裂人设（真实事故）。

    合并规则（确定性裁定，不猜意图；**永不拼接**）：
    - **基准只取原版的规范英文键**——原版里的中文键同样是模型输出的「并行版本」，不能当基准
      （否则修订输出里原样返回的英文拷贝与拼接基准对不上，真改写反而被误判）；
    - 英文键非空且与原版不同 → 采纳英文键（模型按契约把改写写在英文键上）；
    - 否则中文键非空且与原版不同 → 采纳中文键（英文键是「原样返回」的旧拷贝，
      实际改写落在中文键——事故现场）；
    - 都雷同/为空 → 保留原版（「原样返回」的拷贝不等于改动，未被点名的字段
      不因模型的抄写漂移而被覆盖）。
    两个键都与原版不同（真歧义）时取英文键——契约以英文键为准。
    """
    orig_raw = original if isinstance(original, dict) else {}
    orig = {f: (as_text(orig_raw.get(f)) or "").strip() for f in PROFILE_FIELDS}
    rev = revised if isinstance(revised, dict) else {}
    alias_cn = {en: cn for cn, en in _PROFILE_KEY_ALIASES.items()}
    out: dict[str, str] = {}
    for field in PROFILE_FIELDS:
        orig_val = (orig.get(field) or "").strip()
        canon = (as_text(rev.get(field)) or "").strip()
        alt = (as_text(rev.get(alias_cn[field])) or "").strip() if field in alias_cn else ""
        if canon and canon != orig_val:
            out[field] = canon
        elif alt and alt != orig_val:
            out[field] = alt
        else:
            out[field] = orig_val
    return out


def normalize_card(card: dict) -> dict:
    """角色卡整体归一：`profile` 与 `arc.stages[].profile` 的中文键都折进规范英文键。

    阶段卡与基准卡同口径——消费端（界面、`assembler._stage_line`、`autopilot_agent` 的角色摘要）
    一律**只读**六个英文键，中文键写进阶段里同样到不了正文。
    """
    if not isinstance(card, dict):
        return card
    out = dict(card)
    if isinstance(out.get("profile"), dict):
        out["profile"] = normalize_profile(out["profile"])
    arc = out.get("arc")
    stages = arc.get("stages") if isinstance(arc, dict) else None
    if isinstance(stages, list):
        new_arc = dict(arc)
        new_arc["stages"] = [
            ({**st, "profile": normalize_profile(st.get("profile"))}
             if isinstance(st, dict) and isinstance(st.get("profile"), dict) else st)
            for st in stages
        ]
        out["arc"] = new_arc
    return out


def strip_edges(text: str | None) -> str:
    """去掉正文**首尾**的空白（空行、空格、全角空格），中间一字不动。

    为什么必须收口：逐拍写作 + 整章改写会把开头的空行**反复带出并累积**——实测一章开头
    能滚到 20 个 `\\n`，读者一打开正文就是一大片空白（真实事故）。所有落库/注入正文的
    入口都要过这一道。
    """
    return (text or "").strip()


def content_sha(text: str | None) -> str:
    """正文的短哈希（`beat_spans_json` 的失效标记）：正文一旦被改，sha 对不上、
    拍区间即作废（`draft_workflow.beat_spans_of` 的判据）。"""
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def count_words(text: str | None) -> int:
    """正文字数：**去掉所有空白字符**后的长度（与写作/审核口径一致）。

    落库字数必须用这一个口径：`save_content` 此前用 `len(content)`（含换行/空格），
    与写作阶段（去空白）的口径不一致，同一章在「生成结果」与「章节列表」里字数对不上。
    """
    return len("".join((text or "").split()))


def repeat_loop_cut(text: str, *, min_unit: int = 24, max_unit: int = 2000,
                    min_reps: int = 3, window: int = 4000) -> int | None:
    """检测**模型重复循环**：文本尾部同一段被连续重复 ≥`min_reps` 次时，返回「应截断到的下标」
    （**保留第一份**）；没检测到返回 None。

    为什么必须有：模型偶发退化会不停重复同一段直到吃满 `max_tokens` —— 实测一次逐拍写作
    输出 4 万字、同一段重复 148 次，把整章写废（第一章 4.6 万字）。这里用 KMP 求尾部窗口的
    最小周期，O(window)，可随流式频繁调用。

    `min_unit=24` / `min_reps=3` 刻意保守：判据要求**整个尾部窗口都以该周期严格循环**，
    正常行文不可能满足「连续 3 个 ≥24 字完全相同的块」，因此不会误伤。
    """
    n = len(text or "")
    if n < min_unit * min_reps:
        return None
    w = text[-min(n, window):]
    L = len(w)
    # KMP 前缀函数 → 最小周期 p = L - 最长border
    pi = [0] * L
    for i in range(1, L):
        j = pi[i - 1]
        while j and w[i] != w[j]:
            j = pi[j - 1]
        if w[i] == w[j]:
            j += 1
        pi[i] = j
    p = L - pi[-1]
    if p < min_unit or p > max_unit or p * min_reps > L:
        return None
    block = w[L - p:]
    reps = 0
    i = L
    while i - p >= 0 and w[i - p:i] == block:
        reps += 1
        i -= p
    if reps < min_reps:
        return None
    # 保留「第一份」：i 是**端对齐**重复段起点（步长 = p）。
    # - i < p：起点前只剩不足一个块（端对齐相位与开头不同）→ 直接留开头的**一个整块**（[0,p)）；
    # - 其它：正文正常部分（[0,i)）+ 一个块（[i,i+p)）。
    keep = p if i < p else i + p
    return n - L + keep


def collapse_repeats(text: str, *, min_unit: int = 24, max_unit: int = 2000,
                     min_reps: int = 3, max_rounds: int = 50) -> str:
    """落库前的**去重兜底**：把成段重复的内容收敛掉（重复块只留第一份）。

    与 `repeat_loop_cut` 同一判据，循环到无可收敛为止。用于「修改/替换正文」等入口——
    即便流式熔断漏了，也不会把退化稿写进库。
    """
    out = text or ""
    for _ in range(max_rounds):
        cut = repeat_loop_cut(out, min_unit=min_unit, max_unit=max_unit, min_reps=min_reps)
        if cut is None or cut >= len(out):
            break
        out = out[:cut].rstrip()
    return out


def gags_text(gags: list | None) -> str:
    """把情节/章节的「梗/名场面」设计渲染成可读文本（无则空串）。

    梗在情节与细纲阶段设计好，再沿「情节 → 拆章 → 细纲 → 正文」往下传，
    所以渲染要保留「化用出处」与「想引发的读者反应」这两个关键信息。
    """
    if not gags:
        return ""
    lines: list[str] = []
    for gag in gags:
        if isinstance(gag, dict):
            parts = [str(gag.get(k) or "").strip()
                     for k in ("beat", "type", "setup", "punch")]
            line = "｜".join(p for p in parts if p)
            if gag.get("source"):
                line += f"（化用自：{gag['source']}）"
            if gag.get("engagement"):
                line += f"→ 想引发：{gag['engagement']}"
            if line:
                lines.append(f"· {line}")
        elif str(gag).strip():
            lines.append(f"· {gag}")
    return "\n".join(lines)
