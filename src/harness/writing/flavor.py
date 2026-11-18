# 来源：逐字移植自 web_dream backend/app/core/ai_flavor.py（556 行全文，
# d:/project/local_dream/.superpowers/sdd/2026-11-13-p3-writing-pipeline/task-2-brief.md 为需求单；
# 判据/正则/权重/阈值/常量/LEGIT_SAMPLES 一字不改，逐项偏离见 task-2-report.md）
"""AI 味检测：确定性规则，不依赖 LLM。

去 AI 味目前是一次 LLM 判读 + 泛泛的提示词规则：模型对「自己的套路」有盲区，
判读只看正文**末尾 6000 字**，没有评分，改写后也不复检——所以「不是X，是Y」
「那么…那么…」「是……。」这类套路会漏网，甚至被去味改写本身生产出来。

这里用正则与词表在**全篇**范围内点名这些套路，给出可核对的原句与归一化分数，
供写作链在审核阶段并入意见、在去味阶段做定点改写与前后比较。
它是护栏与信号，不做硬拦截，也不替代人类终审。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "LEGIT_SAMPLES",
    "THRESHOLD",
    "FlavorHit",
    "FlavorReport",
    "detect",
    "flavor_brief",
    "rule_names",
]

# 每千字加权命中数；>= 判为「AI 味超标」
THRESHOLD = 6.0

# (套路名, 正则, 权重, 判定解析, 禁用表达, 补充方向)
#   · 判定解析：为什么认出这是 AI 味（给用户/模型看的「理由」）
#   · 禁用表达：正则命中时写进**通用处理方案**的「不许再出现」的描述
#   · 补充方向：可选的额外提示（怎么改更好）
_PATTERN_RULES: list[tuple[str, re.Pattern[str], float, str, str, str]] = [
    ("否定对偶", re.compile(r"不是[^，。！？；：\n]{1,18}?[，。]\s*(?:而)?是"),
     1.0, "先否定再更正的句式，AI 常用它硬造「顿悟感」", "不是……，是……",
     "改直述：直接说是什么，不要先否定再更正"),
    ("二元并置", re.compile(r"(?:那么|如此|这般)[^，。！？\n]{1,6}[，、](?:那么|如此|这般)"),
     1.0, "同构词组左右并置铺陈（那么…那么…）", "那么…那么… 式并置",
     "并置合并成一句白话"),
    ("同构递进", re.compile(r"越[^，。！？\n]{1,8}越[^，。！？\n]{0,8}[，、]\s*越"),
     0.8, "同一句式反复递进（越…越…）", "越…越… 式同构递进",
     "递进保留一层即可"),
    ("动作套话", re.compile(
        r"深吸一口气|深吸了口气|屏住呼吸|勾起嘴角|扯出一抹|扬起嘴角|"
        r"瞳孔[^，。！？\n]{0,4}一缩|心头[^，。！？\n]{0,4}(?:一震|一紧)|身躯一震|"
        r"脊背[^，。！？\n]{0,4}(?:一凉|发凉)|后背发凉|头皮发麻|攥紧(?:了)?拳头|"
        r"握紧了拳|环视(?:四周|一圈|整个)"),
     0.5, "放之四海皆准的通用生理/动作镜头，换谁都能用", "深吸一口气／瞳孔一缩／勾起嘴角 这类动作套话",
     "换成该角色该情境特有的动作，不用通用镜头"),
    ("空泛副词", re.compile(
        r"极其|无比|万分|不由自主|不自觉地|情不自禁|仿佛整个世界"),
     0.4, "程度副词堆砌，看着有情绪其实没有画面", "极其／无比／不由自主 这类空泛副词",
     "换成具体动作/物件/数字/身体反应"),
    # 「弱化副词」单列并归入「只报不改」：缓缓／微微／轻轻 是**正常中文**（「微微一愣」），
    # 用对了是细节而不是错——只作信号、不进改写名单。
    # 真实体检：一段完全正常的动作描写（站起身/缓缓/微微/轻轻）被旧词表判成 32/千字。
    ("弱化副词", re.compile(r"缓缓|微微|轻轻|悄悄|慢慢|淡淡"), 0.2,
     "弱化副词用得密（缓缓／微微／轻轻），画面被磨平", "缓缓／微微／轻轻 这类弱化副词",
     "只留最必要的一处，其余换成具体动作"),
    ("书面套词", re.compile(r"宛如|犹如|恍若|仿若"), 0.4,
     "书面比喻词，白话文里突兀", "宛如／犹如／恍若／仿若", "书面词改白话"),
    ("总结升华", re.compile(
        r"(?:他|她|它)(?:知道|明白)[，,][^。！？\n]{0,24}"
        r"(?:才刚刚开始|刚刚开始|序幕|真正的[^。！？\n]{0,6}才)|"
        r"命运的齿轮|属于[^。！？\n]{0,10}的时代|这一切[^。！？\n]{0,6}(?:才刚刚|才)?开始"),
     1.2, "段末总结升华／命运感，典型的 AI 收尾腔", "……才刚刚开始／命运的齿轮 这类升华句",
     "升华腔直接删，不要替代说法"),
    # 「能力宣告」：用独立短句旁白自夸「主角会什么/这是本能」——正常人不会这么写，
    # 能力该由他**做出来的事**体现（用户反馈：「他会算账。这套本能跟着他一起过来了。」）。
    ("能力宣告", re.compile(
        r"本能(?:跟着|随)(?:他|她|我)|"
        r"(?:他|她|我)(?:的)?老本行|"
        r"(?:他|她|我)(?:最)?(?:擅长|拿手|在行|精通|懂行)|"
        r"(?:他|她|我)干这行|轻车熟路|驾轻就熟|"
        r"(?:这|那)(?:是|正是)(?:他|她|我)(?:的)?(?:本事|拿手好戏|强项|看家本领)"),
     1.0, "用独立短句旁白宣告「主角会什么／这是本能」，靠自夸硬造爽感", "「他会 X。这套本能……」式能力宣告",
     "删掉旁白宣告，用他实际做的事体现能力（该动作/对话里自然露出，不要总结他会什么）"),
    # 「过渡套话」（P0-3，只报不改）：议论文路标词出现在叙事正文里，是 AI 组织段落的
    # 痕迹。引号内不判（对话里「你与此同时还在笑」完全合法）。
    ("过渡套话", re.compile(
        r"值得注意的是|总而言之|不可否认|不得不说|究其根本|与此同时|"
        r"转念一想|思绪万千"),
     0.4, ("议论文/总结报告的路标词（转念一想／总而言之／与此同时），是 AI 在组织段落"
           "而不是人在讲故事"), "转念一想／总而言之／与此同时 这类过渡套话",
     "删掉路标词，让动作与对话自己转折"),
]


_SCORE_ONLY: frozenset[str] = frozenset({
    "否定对偶",      # 「不是A，是B」是合法汉语（对话里极常见），AI 只是滥用
    "弱化副词",      # 缓缓／微微／轻轻 是常用词，用对了是细节
    "书面套词",      # 宛如／犹如 用对了就是好词
    "短句金句化",    # 紧张段落的短句连用是好笔法
    "短句独段",      # 网文主流就是短段落
    "比喻堆叠",      # 比喻本身没错，只是别连用
    "机械复读",      # 分布问题：定点只改一处治不了；且会误伤人名等正常高频词
    "字数元描述",    # 题材相关（现代题材「密码六个字」完全正常）
    "空泛副词",      # 程度副词是否过头，机器判不准
    "过渡套话",      # 路标词在对话里合法（SKIP_IN_QUOTE），旁白里只是信号
    "句长过匀",      # 短句排比（合法文风）句长也匀，只能靠均值门槛豁免，宁漏勿杀
})
# 这些规则在**引号内**（人物对话/引文）不判：对话里的「不是…，是…」「轻轻地」是人物在说话，
# 是自然汉语；只有旁白里的同款才是腔调。真实体检：一句正常对话因此被判 14.7/千字。
_SKIP_IN_QUOTE: frozenset[str] = frozenset(
    {"否定对偶", "弱化副词", "空泛副词", "过渡套话"})
_QUOTED = re.compile(r"[「『“‘\"][^」』”’\"\n]{0,200}[」』”’\"]")


def _inside_quote(at: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= at < b for a, b in ranges)


def _generic_fix(forbidden: str, note: str) -> str:
    """正则命中用的**通用处理方案**：语义不变地重写该片段 + 禁止再出现该类表达。

    为什么不给固定改写文本：同一条规则命中的句子千差万别，写死替换文本会文不对题；
    给「保持语义不变 + 禁用表达」这条**通用约束**，模型按上下文改写才靠谱。
    """
    tail = f"；{note}" if note else ""
    # 不再追加「这类表达」——`forbidden` 本身已是「……这类动作套话」这样的名词短语，
    # 再套一层会变成「……这类动作套话」这类表达（重复）。
    return (f"保持语义不变地重写该片段（只替换这一处，别动上下文）；"
            f"不得再出现「{forbidden}」{tail}")

# 句子切分：按句末标点切开（保留标点），用于「短句金句化」检测
_SENTENCE = re.compile(r"[^。！？!?…\n]+[。！？!?…]?")
# 去掉引号/空白/标点后才是句子的「实义长度」
_TRIM = re.compile(r"[\s\"'“”「」『』（）()【】\[\]—\-…,，。！？!?、；;：:]+")
_SHORT_MAX = 8        # 实义长度 <= 8 字视为短句
_SHORT_RUN_MIN = 3    # 连续 >= 3 个短句才算「金句化」
_SHORT_RUN_WEIGHT = 0.8


@dataclass
class FlavorHit:
    """一次套路命中：**原文 + 判定解析 + 处理方案** + 权重与只报不改标记。

    这三件套是给 LLM/用户直接可用的最小单元：原文是「带 AI 味的内容」，判定解析说明
    「为什么算 AI 味」（命名套路 + 一句人话解释），处理方案是「怎么改」。
    `score_only` 为 True 的命中只计分、不进定点改写名单（`_SCORE_ONLY`）。
    """
    rule: str
    weight: float
    quote: str
    analysis: str
    fix: str
    score_only: bool
    # 命中在正文中的起止偏移（默认 0）。不能一律用 start+len(quote) 推：
    # 「短句金句化」跨句，quote 是各句拼接、掉了句间空白，长度对不上，
    # 定点替换与上下文提取必须靠这两个偏移。
    start: int = 0
    end: int = 0


@dataclass
class FlavorReport:
    score: float            # Σweight / 字数 * 1000，每千字加权命中
    hits: list[FlavorHit]

    @property
    def serious(self) -> bool:
        """是否「需要动手改」：分数超阈值 **且** 至少有一条可改写命中。

        只有「只报不改」类命中（`score_only`：合法汉语／风格偏好／题材相关）时**不算**
        serious——否则一段正常文字会因为弱化副词被判超标，进而触发定点改写/整章润色、
        把好文字改坏（真实体检：5 个合法样本全部超阈值，其中多数纯属误报）。
        """
        return self.score >= THRESHOLD and any(not h.score_only for h in self.hits)

    def targets(self, n: int = 20) -> list[FlavorHit]:
        """去重后的定点改写目标（同套路同原句只留一条）；**跳过「只报不改」类**。

        `flavor_brief()` 的编号与这里的顺序一一对应——定点替换按编号回填，两边必须同源。
        """
        seen: set[tuple[str, str]] = set()
        out: list[FlavorHit] = []
        for hit in self.hits:
            if hit.score_only:
                continue
            key = (hit.rule, hit.quote)
            if key in seen:
                continue
            seen.add(key)
            out.append(hit)
            if len(out) >= n:
                break
        return out


def _short_run_hits(text: str) -> list[FlavorHit]:
    """连续 >= 3 个短句（实义长度 <=8）判为「短句金句化」。

    单独一个短句是好文笔；**连续成串**才是套路——「不是感激。是算账。」「那眼神埃文认得。」
    这种被刻意拆碎、每句一断的强调节奏。
    """
    hits: list[FlavorHit] = []
    run: list[str] = []
    run_start = 0
    run_end = 0

    def flush() -> None:
        if len(run) >= _SHORT_RUN_MIN:
            hits.append(FlavorHit(
                rule="短句金句化", quote="".join(run), start=run_start,
                weight=_SHORT_RUN_WEIGHT,
                analysis="连续 ≥3 个短句独立成句，把句子拆碎当金句的强调节奏",
                fix=_generic_fix("把句子拆碎、每句独立成句当金句", "短句收回句内节奏"),
                score_only="短句金句化" in _SCORE_ONLY,
                end=run_end))

    for m in _SENTENCE.finditer(text):
        piece = m.group()
        if len(_TRIM.sub("", piece)) <= _SHORT_MAX:
            if not run:
                run_start = m.start()
            run.append(piece)
            run_end = m.end()
        else:
            flush()
            run = []
    flush()
    return hits


# 「N个字」类**元描述**：数「字」的个数（「他盯着这三个字」）。两个层次：
#   ① 引号里的原话与数量词**对不上**（「炭窑能抵」是 4 个字，正文却写「这三个字」）——
#      这是**客观错误**，读者一眼看出（用户反馈：读者会默认中文，数错「直接识别出AI」）；
#   ② 没引号的泛提（「他写下三个字」）——架空世界没有汉字，谈「字」的个数/写法本身就是
#      出戏点，但只是提醒（权重低），不当硬错。
# 只认**显式数量词 + 「个字」**：避开「名字/数字/十字路口/八字胡/写字台」这类正常词。
_COUNT_WORD = re.compile(r"([一二两三四五六七八九十0-9]{1,3})个字")
_QUOTE_BEFORE = re.compile(r"[「『“‘\"]([^」』”’\"\n]{1,40})[」』”’\"]")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
_LOOKBACK = 16        # 引号与「N个字」之间允许隔的字数（如「炭窑能抵」他盯着这三个字）


def _num_of(word: str) -> int | None:
    """「三 / 十二 / 3」→ 整数；解析不出（如「十几」）返回 None，此时不做字数比对。"""
    if word.isdigit():
        return int(word)
    if "十" in word:
        head, _, tail = word.partition("十")
        tens = _CN_NUM.get(head, 1) if head else 1
        ones = _CN_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_NUM.get(word)


def _char_count_hits(text: str) -> list[FlavorHit]:
    """「这几个字」类元描述：与引号内原话字数**对不上**是硬错，无引号的泛提只算提醒。"""
    hits: list[FlavorHit] = []
    for m in _COUNT_WORD.finditer(text):
        word = m.group(1)
        num = _num_of(word)
        win_start = max(0, m.start() - _LOOKBACK)
        window = text[win_start:m.start()]
        q = None
        for cand in _QUOTE_BEFORE.finditer(window):
            q = cand                      # 取窗口里**最后一个**引号：紧挨着它的那句原话
        if q is not None and num is not None:
            inner = q.group(1).strip()
            n_han = len(re.sub(r"\W", "", inner))     # 只数汉字/字母数字（不含标点）
            n_raw = len(inner)
            if num in (n_han, n_raw):
                continue                   # 字数对得上：内部自洽，不算问题
            start = win_start + q.end()
            hits.append(FlavorHit(
                rule="字数对不上",
                quote=text[start:m.end()], start=start, end=m.end(), weight=1.2,
                analysis=(f"引号里是「{inner}」（{n_han} 个字），正文却写「{word}个字」——"
                          "字数对不上，读者一眼看出是硬凑；架空题材还会暴露「汉字」预设"),
                fix=_generic_fix("把引号里原话的字数写错（异世界读者默认中文，数错即出戏）",
                                 "删掉这句字数描述，或直接引用原话、不要报字数"),
                score_only="字数对不上" in _SCORE_ONLY))
            continue
        hits.append(FlavorHit(
            rule="字数元描述",
            quote=m.group(), start=m.start(), end=m.end(), weight=0.4,
            analysis="谈「字」的个数/写法：架空世界没有汉字，读者会默认中文，与实际不符即出戏",
            fix=_generic_fix("在架空/异世界正文里数「字」的个数或谈字形",
                             "删掉，或改成「这句话／这几个词」"),
            score_only="字数元描述" in _SCORE_ONLY))
    return hits


# ---- 下面四条是 2026-09-30 补的：用户实测段落（959 字）里这四类全漏，而现有规则只给它
#      0.4/千字（阈值 6）——「编号清点 / 极短句独段 / 机械复读 / 比喻堆叠」。
# 编号枚举：把叙述写成清单（第N个念头／第N次…）。正则容忍中间修饰语
# （「第一个**冒出来的**念头」，只写 `第N个念头` 会漏配）。
_ORDINAL_LIST_RE = re.compile(
    r"第\s*[一二两三四五六七八九十0-9]{1,3}\s*(?:个)?\s*[^，。！？；：\n]{0,6}?"
    r"(?:念头|想法|次|遍|步|条|点|件事|个理由|个方向|个问题)")
_ORDINAL_MAX_SPAN = 240      # 同一份清单里首尾最多隔这么多字
_SHORT_PARA_MAX = 6          # 「极短句独段」：实义长度 <= 6 字
_SHORT_PARA_MIN = 4          # 至少这么多处，且密度 >= 3/千字，才算成片的强调节奏
_SIMILE_RE = re.compile(r"仿佛|如同|好像|像")
_SIMILE_MIN = 4              # 明喻至少这么多处，且密度 >= 3/千字
_DENSITY_MIN = 3.0
_REPEAT_MIN = 4              # 同一实义短语出现这么多遍才算机械复读
_REPEAT_STOP = {             # 高频虚词/套话：排除掉，否则「他说」「一个」会把规则变成噪音
    "一个", "什么", "自己", "不是", "已经", "但是", "可是", "然后", "因为", "所以",
    "这个", "那个", "时候", "起来", "出来", "进去", "回来", "一样", "只是", "就是",
    "还是", "没有", "这里", "那里", "一声", "一会", "回头", "抬头", "低头", "听到",
    "看到", "觉得", "知道", "说了", "看着", "点了", "一下", "过来", "一块", "下去",
}
_ORDINAL_HEAD = re.compile(r"^第[一二两三四五六七八九十0-9]+")


def _density(n: int, text: str) -> float:
    return n / max(1, len(text)) * 1000


def _numbered_hits(text: str) -> list[FlavorHit]:
    """编号枚举：把叙述写成清单（第一个念头…第二个…／第一次…第二次…）。"""
    ms = list(_ORDINAL_LIST_RE.finditer(text))
    if len(ms) < 2:
        return []
    start = ms[0].start()
    end = min(len(text), ms[-1].end())
    if end - start > _ORDINAL_MAX_SPAN:
        end = start + _ORDINAL_MAX_SPAN
    return [FlavorHit(
        rule="编号枚举", quote=text[start:end], start=start, end=end, weight=1.0,
        analysis=(f"把叙述写成**编号清单**（{len(ms)} 处「第N…」：第 N 个念头／第 N 次）——"
                  "用编号制造层次感；真人回忆不会这么点数目"),
        fix=_generic_fix("用「第 N 个念头／第 N 次」编号清点地叙述",
                         "改成连贯叙述：跟着当时的注意力走、不要编号，确需分点就落到动作或对话"),
        score_only="编号枚举" in _SCORE_ONLY)]


def _short_para_hits(text: str) -> list[FlavorHit]:
    """极短句独占段落：偶有一处是好笔法，**成片出现**才是 AI 的强调节奏。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    shorts = [p for p in paras if len(_TRIM.sub("", p)) <= _SHORT_PARA_MAX]
    if len(shorts) < _SHORT_PARA_MIN or _density(len(shorts), text) < _DENSITY_MIN:
        return []
    hits: list[FlavorHit] = []
    for p in shorts[:3]:
        at = text.find(p)
        if at < 0:
            continue
        hits.append(FlavorHit(
            rule="短句独段", quote=p, start=at, end=at + len(p), weight=0.8,
            analysis=(f"极短句独立成段（全篇 {len(shorts)} 处，{_density(len(shorts), text):.1f}/千字）"
                      "——把句子拆碎独段做强调，是 AI 的节奏套路"),
            fix=_generic_fix("极短句（如「烫。」「字灭了。」）独立成段做强调",
                             "并回上一段，或补成有信息的完整句"),
            score_only="短句独段" in _SCORE_ONLY))
    return hits


def _simile_hits(text: str) -> list[FlavorHit]:
    """比喻堆叠：短距离内连用明喻（口语的「像」现有规则完全没管）。"""
    ms = list(_SIMILE_RE.finditer(text))
    if len(ms) < _SIMILE_MIN or _density(len(ms), text) < _DENSITY_MIN:
        return []
    hits: list[FlavorHit] = []
    for m in ms[:3]:
        s_start = max(text.rfind("。", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        ends = [p for p in (text.find(c, m.end()) for c in "。！？\n") if p >= 0]
        s_end = (min(ends) + 1) if ends else len(text)
        quote = text[s_start:s_end].strip()
        if not quote:
            continue
        hits.append(FlavorHit(
            rule="比喻堆叠", quote=quote, start=s_start, end=s_end, weight=0.6,
            analysis=(f"短距离内连用明喻（全篇 {len(ms)} 处「像/仿佛/如同」，"
                      f"{_density(len(ms), text):.1f}/千字）——用比喻代替描写，越比越空"),
            fix=_generic_fix("在一段里连用多个明喻（像…像…像…）",
                             "只留最准的那一处比喻，其余改成具体的动作、物件或身体感受"),
            score_only="比喻堆叠" in _SCORE_ONLY))
    return hits


def _repeat_hits(text: str) -> list[FlavorHit]:
    """机械复读：同一实义短语反复出现（「太阳穴」×4、「念头」×4）——填充篇幅的典型症状。

    定点改写只换一处、治不了分布，所以这条**主要靠把分数顶过阈值**，让整章润色兜底跑起来。
    """
    grams: dict[str, int] = {}
    for n in (2, 3, 4, 5):
        for i in range(len(text) - n + 1):
            g = text[i:i + n]
            if not re.fullmatch(r"[一-龥]+", g):
                continue
            if g in _REPEAT_STOP or _ORDINAL_HEAD.match(g):
                continue
            grams[g] = grams.get(g, 0) + 1
    # 只留最长的那个形态（「太阳穴」命中时不再重复报「太阳」）；再要一道密度门槛，
    # 免得长章节里「某个名字出现 4 次」这种正常情况也被点名。最多报 2 条（§3.2「0.6 × ≤2」）。
    top = [g for g, c in sorted(grams.items(), key=lambda kv: (-kv[1], -len(kv[0])))
           if c >= _REPEAT_MIN and _density(c, text) >= 1.5][:2]
    picked: list[str] = []
    for g in top:
        if any(g in p for p in picked):
            continue
        picked.append(g)
    hits: list[FlavorHit] = []
    for g in picked:
        at = text.find(g)
        s_start = max(text.rfind("。", 0, at), text.rfind("\n", 0, at)) + 1
        hits.append(FlavorHit(
            rule="机械复读", quote=text[s_start:at + len(g)], start=s_start,
            end=at + len(g), weight=0.6,
            analysis=(f"「{g}」全篇出现 {grams[g]} 次——同一细节反复复述是在填篇幅，"
                      "读者会觉得原地打转（**这条只作信号**：也可能是人名等正常高频词，"
                      "要不要动由你判断；定点改写也不治分布）"),
            fix=_generic_fix(f"反复复述同一处细节（「{g}」重复 {grams[g]} 次）",
                             "换个说法/换个感官，或把重复的那几处合并成一处"),
            score_only="机械复读" in _SCORE_ONLY))
    return hits


def _even_sentence_hits(text: str) -> list[FlavorHit]:
    """句长过匀（P0-3，只报不改）：一段里长句的句长整齐划一是 AI 腔的统计特征。

    阈值是**实测**出来的（webdream 本地库 68 个 ≥5 句段落）：最匀的真人长句段 CV≈0.19，
    与刻意的短句排比（合法文风，CV≈0.21）**不可分**——分界靠「平均实义长 ≥10」：
    排比段全是短句（mean≈5）天然豁免。宁漏报不误杀（§6 风险表：过不去就不进代码）。
    """
    hits: list[FlavorHit] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        lens = [len(_TRIM.sub("", m.group())) for m in _SENTENCE.finditer(para)]
        lens = [l for l in lens if l > 0]
        if len(lens) < 5:
            continue
        mean = sum(lens) / len(lens)
        if mean < 10:
            continue                     # 短句排比段：mean≈5，豁免
        cv = (sum((l - mean) ** 2 for l in lens) / len(lens)) ** 0.5 / mean
        if cv >= 0.25:
            continue
        at = text.find(para)
        if at < 0:
            continue
        first = _SENTENCE.search(para)
        quote = first.group().strip() if first else para[:24]
        hits.append(FlavorHit(
            rule="句长过匀", quote=quote, start=at, end=at + len(quote), weight=0.4,
            analysis=(f"一段 {len(lens)} 句、平均 {mean:.0f} 字、句长变异系数 {cv:.2f}——"
                      "长句整齐划一是 AI 生成文本的统计特征（真人写作句长忽长忽短）"),
            fix=_generic_fix("把每一段都写成均匀等长的句子",
                             "打破均匀：长句里拆出短句、短句后接长句，跟着呼吸走"),
            score_only="句长过匀" in _SCORE_ONLY))
        if len(hits) >= 2:               # 只报不改类封顶 2 条（§3.2 惯例）
            break
    return hits


def detect(text: str) -> FlavorReport:
    """对全篇正文做确定性 AI 味检测，返回命中清单与每千字加权分数。"""
    text = text or ""
    hits: list[FlavorHit] = []
    quoted = [(m.start(), m.end()) for m in _QUOTED.finditer(text)]
    for name, pattern, weight, analysis, forbidden, note in _PATTERN_RULES:
        fix = _generic_fix(forbidden, note)
        for m in pattern.finditer(text):
            # 引号内的对话不判（「不是…，是…」「轻轻地」是人物在说话）——只对白名单规则收窄
            if name in _SKIP_IN_QUOTE and _inside_quote(m.start(), quoted):
                continue
            hits.append(FlavorHit(rule=name, quote=m.group(), start=m.start(),
                                  weight=weight, analysis=analysis, fix=fix,
                                  score_only=name in _SCORE_ONLY, end=m.end()))
    hits.extend(_short_run_hits(text))
    hits.extend(_char_count_hits(text))
    hits.extend(_numbered_hits(text))
    hits.extend(_short_para_hits(text))
    hits.extend(_simile_hits(text))
    hits.extend(_repeat_hits(text))
    hits.extend(_even_sentence_hits(text))
    chars = len(text)
    total = sum(h.weight for h in hits)
    score = round(total / chars * 1000, 1) if chars else 0.0
    hits.sort(key=lambda h: h.start)
    return FlavorReport(score=score, hits=hits)


# ---- 误杀体检样本（§3.3）：这些**本来都合理**，旧规则却全判成超标。作为「误杀门」的尺子，
#      回归测试与经验库沙箱共用同一份，避免两处口径漂移。
LEGIT_SAMPLES: list[str] = [
    "“我不是不想去，是去不了。”他把碗往桌上一放，“家里那摊子事你也看见了。”",
    "林恩把账册合上。林恩想了想，又把账册翻开。林恩在灯下算了半个时辰。林恩把笔搁下。",
    "他站起身，缓缓走到门口，微微掀开帘子看了一眼。外面在下雨，他轻轻把帘子放下，又坐回原处。",
    "他冲过去。刀光一闪。血溅在他脸上。他来不及擦。第二刀已经到了。他往旁边滚。",
    "密码是六个字：他母亲的名字加生日的月份。他写在纸条上，折了两折塞进钱夹最里面。",
]


def rule_names() -> list[str]:
    """当前生效的**全部规则名**（18 条，与附录 D 表一一对应）。

    经验库用它避免把新候选归纳成一条已存在的规则；测试也靠它守住「规则清单不漂移」。
    函数式规则的名称是硬编码的 `FlavorHit(rule=...)` 字面量，
    这里与附录 D 表同步维护（改动规则时两处一起改）。
    """
    return [r[0] for r in _PATTERN_RULES] + [
        "短句金句化", "字数对不上", "字数元描述", "编号枚举",
        "短句独段", "比喻堆叠", "机械复读", "句长过匀",
    ]


def flavor_brief(text: str, targets: list[FlavorHit]) -> str:
    """把去味目标渲染成编号清单（**与定点替换同源**：编号一一对应）。

    逐条附上**前后各若干字上下文**——只给被点名片段、不给前后文时，模型容易
    把 replacement 写长而与相邻文本重复；带上下文它才能只替换该片段、不越界。
    编号与 `FlavorReport.targets()` 一一对应——定点替换按编号回填，两边必须同源。
    """
    lines: list[str] = []
    for i, h in enumerate(targets, 1):
        quote = h.quote if len(h.quote) <= 40 else h.quote[:40] + "…"
        ctx = ""
        if text:
            end = h.end or (h.start + len(h.quote))
            pre = text[max(0, h.start - 24):h.start]
            post = text[end:end + 24]
            if pre or post:
                ctx = f"（上文…{pre}｜下文{post}…）"
        lines.append(f"{i}. 原文「{quote}」{ctx}｜判定：{h.rule}——{h.analysis}｜处理：{h.fix}")
    return "\n".join(lines)
