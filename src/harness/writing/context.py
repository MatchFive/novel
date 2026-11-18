# 来源：语义移植自 web_dream（d:/project/web_dream/backend/app/llm/assembler.py，
# 行号锚点见 task-5-brief.md；标注「seed N-M 逐字」的渲染判据不改，逐项偏离见
# task-5-report.md）。story 设定复用 planning.context._story_settings / planning.pure.story_block，
# AI 味命中复用 writing.flavor，均 import 不复制。
"""写作链上下文组装（P3 Task 5）——**store → LLM 提示词**的接缝（P3-lite 确定性版）。

相对 seed 的降级：seed 的混合检索（BM25+语义重排）在本地没有对应物，本模块全部改为
**确定性注入**——world/角色由 `task.context_select` 挑出的标题/名字精确匹配，命中形态
逐字保留 seed 的注入格式（`catalog` 337-350、`cast_cards` 483-548、`_beats_text` 642-695、
`build_draft_prompt` 189-334 的 P3 存活子集）。记忆/摘要槽 P4 已接线（`build_memory_bundle`
五块 + digest 前情提要，Task 7）；教程/联网/梗等 P5 槽位留空串不占位渲染
（`# TODO(P5)` 注释标留位点）。

预算裁剪（seed 293-311 逐字）：droppable 区块按「丢弃优先级」顺序累加，超出
`BUDGET_CHARS` 即丢（recent_draft 例外——降级保留尾 3000 不丢）；core + task 永不丢
（任务指令带「只写这一拍」硬约束，裁掉会让模型失去长度约束冲到 ~2 万字，seed 真实事故）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.memory.digest import DigestKeeper
from harness.memory.inject import (
    build_memory_bundle,
    filter_future_digest,
    state_line,
    world_state_block,
)
from harness.planning.context import _story_settings
from harness.planning.pure import story_block
from harness.prompts.loader import PromptLoader
from harness.skills import load_skill_by_dir
from harness.store.frontmatter import FileFormatError, split_file
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import PROFILE_FIELDS, as_list, as_text, normalize_profile
from harness.writing.flavor import FlavorHit, FlavorReport
from harness.writing.pure import BUDGET_CHARS, TAIL_BEAT, TAIL_PREV_CHAPTER

__all__ = [
    "DraftContext",
    "beats_text",
    "build_deaify_edits_vars",
    "build_deaify_vars",
    "build_draft_context",
    "build_review_vars",
    "build_revise_vars",
    "build_rewrite_beat_vars",
    "cast_cards_block",
    "catalog",
    "chapter_memory_bundle",
    "context_select_vars",
    "project_style_body",
    "world_entries_block",
]

# 正文 `键：值` 行扫描（planning.context._KV_RE 同型；跨模块不引私有名）
_KV_RE = re.compile(r"^([^\s:：]+)[:：]\s*(.*)$")
# 渲染模板后的 {{var}} 清理 + 空区块头清理（seed _clean_placeholders 729-741 逐字）
_VAR_RE = re.compile(r"\{\{\s*\w+\s*\}\}")
_BLOCK_HEAD_RE = re.compile(r"【[^】]*】")

# cast 注入口径（seed assembler.py:49/53）：每卡六 profile 键、每键 400、别名 200、top8。
# 六键顺序取 seed :519-521 的展示序（与 harness.text.PROFILE_FIELDS 集合一致）。
_CAST_TOP_K = 8
_CAST_FIELD_MAX = 400
_CAST_ALIAS_MAX = 200
_UNCARDED_MAX = 15
_FIELD_LABELS = (("性格", "personality"), ("外貌", "appearance"), ("背景", "background"),
                 ("动机", "motivation"), ("能力", "ability"), ("说话风格", "speech_style"))
assert {key for _, key in _FIELD_LABELS} == set(PROFILE_FIELDS)

# 死亡/退场（seed assembler.py:110-112 逐字）：**本章未被点名**时该角色不进角色卡块
# （回顾/插叙章点名者仍注入）。职责边界（裁定）：过滤只作用于**角色卡组装层**，
# 记忆区块（MemoryBundle）不过滤死亡 POV/角色的经历。
_OFFSTAGE_STATUS = ("dead", "exited")

_PAREN_RE = re.compile(r"（[^）]*）|\([^)]*\)")


def _chapter_cast_names(meta: dict, beats: list) -> list[str]:
    """本章细纲点名的人物名（seed _chapter_cast_names:565-587 的确定性降级）。

    meta.characters（dict/str 容错）+ 各节拍 pov，去括号注释、去重保序；
    群像/若干等集合词不进名单（命中不了具体角色卡）。
    """
    names: list[str] = []
    raw = (meta or {}).get("characters")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            n = item.get("name") if isinstance(item, dict) else str(item)
            if n:
                names.append(str(n))
    for b in as_list(beats):
        if isinstance(b, dict) and b.get("pov"):
            names.append(str(b["pov"]))
    out: list[str] = []
    for n in names:
        n = _PAREN_RE.sub("", n).strip()
        if not n or any(k in n for k in ("群像", "若干", "等", "群", "角色")):
            continue
        if n not in out:
            out.append(n)
    return out


def _last_seen(entry: dict) -> int:
    """角色最后出场章号（seed _last_seen:115-121 逐字；无 state 视为 0）。"""
    try:
        return int((entry or {}).get("last_seen") or 0)
    except (TypeError, ValueError):
        return 0


def _cast_order(names: list[str], named: list[str], states: dict) -> list[str]:
    """seed _cast_order:589-623 的名字表降级：细纲点名者（pov/出场表/节拍 pov）
    按点名序最前，其余按 ``last_seen_chapter`` **降权**排序（最近出场者优先，
    久未出场/已下线的自然让出额度；无 state 键 0，稳定排序保持原序）。"""
    pool = [n for n in names if n]
    matched: list[str] = []
    for want in named:
        hit = next((n for n in pool
                    if n not in matched and (n == want or want in n or n in want)), None)
        if hit is not None and hit not in matched:
            matched.append(hit)
    rest = [n for n in pool if n not in matched]
    rest.sort(key=lambda n: _last_seen((states or {}).get(n) or {}), reverse=True)
    return matched + rest


def _cast_offstage_filter(names: list[str], named: list[str], states: dict) -> list[str]:
    """生存过滤（seed :507-510）：dead/exited 且**细纲未点名** → 不出卡。"""
    named_l = {w.lower() for w in named}
    out: list[str] = []
    for n in names:
        status = as_text(((states or {}).get(n) or {}).get("status")) or ""
        if status.strip() in _OFFSTAGE_STATUS and not any(
                w in n.lower() or n.lower() in w for w in named_l):
            continue
        out.append(n)
    return out

# world 注入口径（brief 钉死）：精确标题匹配、top5、content[:150]
_WORLD_TOP_K = 5
_WORLD_CONTENT_MAX = 150

# seed llm_service.py:33 _CREATIVE_TASKS 的写作类口径（P3 存活子集）：创作方法论只拼到
# 规划/写作任务的 system 上（审核/去味注入纯属 token 消耗，还可能带偏分析腔）。
_CREATIVE_TASK_KEYS = frozenset({
    "task.draft_continue", "task.draft_revise", "task.draft_rewrite_beat",
})

# P4 空槽已接线（2026-11-14 清理）：`other_char_events`/`related_events`/`open_promises`/
# `rolling_summary`/`pov_memories`/`knowledge_state` 六键由 build_draft_context 在 mem 给时
# 实填（块由代码物理拼接、不经模板），mem=None 维持空串不渲染（旧测试兼容）。
# `handbook`（教程手册）随 P5 skills 体系落地——改为**定向注入**（skills.skill_injection），
# 不再是模板空槽；`web_facts`（联网核查事实）由 P5 T10 填（落 research.md → 注入块）；
# `meme_hint`（梗库）随设计砍除，不留槽。
#
# 旧的 `_P4_P5_EMPTY_SLOTS` dict 全仓无运行期消费（只有注释引用），本期随 P5 收口删除。


@dataclass
class DraftContext:
    """一次成稿组装的产物：system / user 双提示词 + review/修正/deaify 复用的材料。"""
    system: str
    user: str
    materials: dict = field(default_factory=dict)


# ================================================================ 基础工具（seed 逐字）

def _fmt(title: str, content: str) -> str:
    """区块包装（seed _fmt 723-726 逐字）：内容空 → 整个区块空串（调用方直接不拼）。"""
    content = (content or "").strip()
    return f"{title}\n{content}" if content else ""


def project_style_body(store: ProjectStore) -> str:
    """项目级 ``style`` skill 的正文（无则空串）——**文风只认项目级**（设计 §2.6 明文：
    用户级 style 不注入写作）。每次现读 SKILL.md：**改动即生效**，不缓存。

    直读那一个文件（``load_skill_by_dir``，**不遍历目录**）：``build_draft_context`` 每拍
    调一次，目录遍历不得进事件循环。目录名即查找键——目录名是 style 但 frontmatter 写了
    别的名字时，文风钩子**不得静默失效**（M-12）。
    """
    skill = load_skill_by_dir(store.root, "style", scopes=("project",))
    return skill.body if skill is not None else ""


# M-12：预算降级保留的切分点落在**句末标点**（不切在词句中间）
_SENTENCE_END_CHARS = "。！？!?\n"
# 句末标点回退窗口：窗口内找不到句末标点才硬切（不凭空造边界）
_SENTENCE_WINDOW = 100


def _degrade_recent_draft(text: str, *, limit: int = 3000) -> str:
    """最近正文的预算降级保留：取尾部 limit 字，**切分点落在句末标点**。

    与旧版硬切（``text[-limit:]``）的差别：硬切会把首句拦腰截断，模型看到的是半句开头；
    本版从窗口首部起找最近的句末标点（。！？!?\\n），从该标点**之后**开始保留；
    前 100 字内没有句末标点 → 硬切（原文没有句号可依，不能凭空造边界）。
    """
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    for idx in range(min(_SENTENCE_WINDOW, len(tail))):
        if tail[idx] in _SENTENCE_END_CHARS:
            return tail[idx + 1:]
    return tail


def _clean_placeholders(text: str) -> str:
    """删除未填充的 {{占位符}} 与空区块标题行，保留任务指令与铁律（seed 729-741 逐字）。

    组装层不产出的变量传 ""（seed 同款），模板里的空区块头由这里物理清掉——块内容由
    代码在 user 里拼接，不占模板位置。
    """
    text = _VAR_RE.sub("", text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _BLOCK_HEAD_RE.fullmatch(line):  # 空区块标题（如【风格指南】）
            continue
        out.append(line)
    return "\n".join(out)


def _loader(store: ProjectStore) -> PromptLoader:
    """项目级 > builtin 两级查找；home 指向项目根本身（跳过用户级目录）。

    偏离 seed（用户家目录兜底）：组装必须可复现——开发机 `~/.harness/prompts` 里的私货
    混进测试会让渲染断言漂移；项目级 `.harness/prompts` 覆盖口保留。
    """
    return PromptLoader(project_root=store.root, home_dir=store.root)


def _creative_system(loader: PromptLoader, prompt_key: str, base: str) -> str:
    """创作方法论只拼到写作类任务的 system 上（seed _with_creation_method 131-141 语义）。

    模板缺失不炸（seed try/except 同款）：本地 builtin 尚无 global.creation_method，
    加载不到就原样返回 base，注入口径（写作类必带）在此留位。
    """
    if prompt_key not in _CREATIVE_TASK_KEYS:
        return base
    try:
        extra = loader.get("global.creation_method")
    except (KeyError, OSError):
        return base
    extra = extra.strip()
    return f"{base}\n\n{extra}" if extra else base


# ================================================================ catalog（seed 337-350 逐字）

def catalog(store: ProjectStore) -> str:
    """资源目录（紧凑版）：供「上下文策划」模型挑选要注入的内容（seed 337-350 逐字）。

    设定 30 条（content[:40]）+ 角色 15（background[:40]，逐卡回读容错）+ 章摘要 15。
    本地无摘要链 → 摘要节只有节头（chapter meta 无 summary 键，行过滤后为空）。
    """
    lines = ["【设定条目】"]
    lines.extend(f"- {e['title']}：{(e.get('content') or '')[:40]}"
                 for e in store.read_world()[:30])
    lines.append("【角色】")
    for card in store.list_characters()[:15]:
        background = ""
        try:
            full = store.read_character(card["name"])
        except (FileNotFoundError, FileFormatError):
            full = {}
        profile = full.get("profile") if isinstance(full.get("profile"), dict) else {}
        background = str(profile.get("background") or "")[:40]
        lines.append(f"- {card['name']}（{card.get('role_type') or '?'}）：{background}")
    lines.append("【章节摘要】")
    lines.extend(f"- 第{c.seq}章「{c.meta.get('title') or ''}」："
                 f"{str(c.meta.get('summary') or '')[:50]}"
                 for c in store.list_chapters()[:15] if c.meta.get("summary"))
    return "\n".join(lines)


# ================================================================ beats_text（seed 642-695 逐字）

def beats_text(meta: dict, beats: list, target_beat: str = "", scene_note: str = "") -> str:
    """本章细纲块：「场景/出场人物/编号情节/对话要点/爽点/结尾钩子」结构（seed 逐字）。

    对齐本地微调模型的细纲→正文训练格式；target_beat 命中时追加「⚠️ 本次只写第 N 拍」锁
    （场景锁定，只出现本拍涉及的人物与状态）与「🚫 后续节拍尚未发生」清单（防「该醒的人
    提前醒」）。scene_note（P1-6 场景回喂）非空时随锁追加一句说明。
    """
    beats = [b for b in as_list(beats) if isinstance(b, dict)]
    if not beats:
        return f"（目标节拍：{target_beat}）" if target_beat else ""
    meta = meta or {}
    parts: list[str] = []
    if meta.get("scene"):
        parts.append(f"场景：{meta['scene']}")
    chars = meta.get("characters")
    if chars:
        if isinstance(chars, list):
            chars = "；".join(
                f"{c.get('name', '')}（{c.get('role', '')}）" if isinstance(c, dict) else str(c)
                for c in chars)
        parts.append(f"出场人物：{chars}")
    parts.append("情节（按顺序扩写）：")
    parts.extend(f"{i + 1}. {b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}"
                 for i, b in enumerate(beats))
    if meta.get("dialogue_hooks"):
        parts.append("对话要点：" + "；".join(str(x) for x in as_list(meta["dialogue_hooks"])))
    if meta.get("humor_points"):
        parts.append("爽点设计：" + "；".join(str(x) for x in as_list(meta["humor_points"])))
    if meta.get("ending_hook"):
        parts.append(f"结尾钩子：{meta['ending_hook']}")
    if target_beat:
        cur_idx = None
        for i, b in enumerate(beats):
            ev = str(b.get("event") or "")
            if target_beat == ev or (target_beat and target_beat in ev) or (ev and ev in target_beat):
                cur_idx = i
                break
        if cur_idx is not None:
            cur = beats[cur_idx]
            parts.append(
                f"⚠️ 本次只写第 {cur_idx + 1} 拍：{cur.get('pov', '')}｜{cur.get('location', '')}｜{cur.get('event', '')}"
                f"——场景锁定「{cur.get('location', '')}」，只出现本拍涉及的人物与状态"
                + (f"；{scene_note}" if scene_note else ""))
            later = [str(b.get("event") or "") for b in beats[cur_idx + 1:] if b.get("event")]
            if later:
                parts.append("🚫 后续节拍尚未发生（严禁提前写其中的人物登场/苏醒/揭示/事件）："
                             + "；".join(later))
        else:
            parts.append(f"→ 待续写节拍：{target_beat}")
    return "\n".join(parts)


# ================================================================ cast_cards_block（seed 483-548 确定性降级）

def _card_profile(meta: dict, body: str) -> dict:
    """从角色卡 front-matter + 正文 `键:值` 行扫六 profile 键（harness.text.PROFILE_FIELDS）。

    三来源容错合并（后者不覆盖前者已有值）：front-matter 的 profile 子 dict → front-matter
    平铺键（P0 KV 卡）→ 正文 KV 行（E.4 三节卡，含中文键名）。中文键由 normalize_profile
    折进规范英文键；缺键静默跳过。
    """
    raw: dict = {}
    prof = meta.get("profile")
    if isinstance(prof, dict):
        raw.update(prof)
    for key, value in meta.items():
        if key != "profile" and not isinstance(value, (dict, list)):
            raw.setdefault(key, value)
    for line in (body or "").splitlines():
        m = _KV_RE.match(line.strip())
        if m:
            raw.setdefault(m.group(1), m.group(2))
    return normalize_profile(raw)


def _alias_text(meta: dict) -> str:
    """aliases 归一为顿号串（list/str/None 容错），空白返回空串。"""
    aliases = meta.get("aliases")
    if isinstance(aliases, list):
        return "、".join(str(a).strip() for a in aliases if str(a).strip())
    return (as_text(aliases) or "").strip()


def cast_cards_block(store: ProjectStore, names: list, *,
                     states: dict | None = None) -> str:
    """出场角色卡注入（seed _retrieve_cast 483-548 的确定性降级）。

    每张卡：`- 名（定位）｜别名/真名:aliases[:200]` + 六 profile 键逐行 `  标签:值[:400]`
    （缺键静默跳过）；names 取前 top8，无卡名进「本章还出场（**没有角色卡**）」名单 [:15]
    （seed 逐字提醒：沿用细纲里的名字，不要另起名/改名）。``states``（P4 接线：
    MemoryStore.read_state()["characters"]，name→state）非空时，有现状的角色卡附
    「【当前状态（实际，以此为准）】」行（seed :536-540 附卡尾；过滤/排序由调用方
    _cast_offstage_filter/_cast_order 在此**之前**完成）。
    相对 seed 的降级：无成长时间轴（P4 角色现状链）——names 由调用方
    （context_select 选出 + 细纲点名）给定，不随库检索。
    """
    names = [n for n in (str(n).strip() for n in as_list(names)) if n]
    if not names:
        return ""
    lines: list[str] = []
    uncarded: list[str] = []
    for i, name in enumerate(names):
        try:
            meta, body = split_file(store._character_file(name))  # 同包内部路径助手
        except (FileNotFoundError, FileFormatError):
            uncarded.append(name)
            continue
        if i >= _CAST_TOP_K:  # 超 top8 不回读渲染，只确认有卡（seed uncarded 判据同口径）
            continue
        head = f"- {name}（{as_text(meta.get('role_type')) or '?'}）"
        alias = _alias_text(meta)[:_CAST_ALIAS_MAX]
        if alias:
            head += f"｜别名/真名：{alias}"
        parts = [head]
        profile = _card_profile(meta, body)
        for label, key in _FIELD_LABELS:
            value = profile.get(key)
            if value:
                parts.append(f"  {label}：{str(value)[:_CAST_FIELD_MAX]}")
        if states:
            line = state_line(name, states.get(name) or {})
            if line:
                parts.append(line)
        lines.append("\n".join(parts))
    if uncarded:
        lines.append("- 本章还出场（**没有角色卡**）："
                     + "、".join(uncarded[:_UNCARDED_MAX])
                     + "——请**沿用细纲里的这些名字**，不要另起名/改名，也不要与卡上角色弄混")
    return "\n".join(lines)


# ================================================================ world_entries_block

def world_entries_block(store: ProjectStore, world_titles: list) -> str:
    """标题精确匹配 → top5、`content[:150]`（brief 钉死；content 空退回摘要键）。

    空入参/全无命中 → 空串（调用方 `_fmt` 判空后整个区块不出现）。与 P2 `_world_text`
    同渲染形态 `【标题】内容[:150]`。
    """
    titles = [t for t in (str(t).strip() for t in as_list(world_titles)) if t]
    if not titles:
        return ""
    entries = store.read_world()
    lines: list[str] = []
    for title in titles:
        entry = next((e for e in entries if e.get("title") == title), None)
        if entry is None:
            continue
        content = (entry.get("content") or entry.get("summary") or "")[:_WORLD_CONTENT_MAX]
        lines.append(f"【{entry['title']}】{content}")
        if len(lines) >= _WORLD_TOP_K:
            break
    return "\n".join(lines)


# ================================================================ task 指令与材料渲染

def _task_instruction(target_beat: str) -> str:
    """【任务】块文案（seed build_draft_prompt 278-287 的形状；字数口径裁剪）。

    偏离 seed:seed 的「本章目标约 N 字 / 本拍约 N 字」来自 gen_config 的 target_words，
    该参数不在 Task 5 签名里（P3-lite 由 beat_word_budgets 在调用侧给 max_tokens），故只留
    「写完即停 / 不要写后续节拍」的硬约束。
    """
    if target_beat:
        return (f"只写「{target_beat}」对应的**这一拍**：写完即停——"
                "**不要**接着写后续节拍、不要给全章收尾。")
    return "把【本章细纲】完整扩写成本章正文，按节拍顺序推进，结尾落在结尾钩子上。"


def _issues_text(issues) -> str:
    """审核意见编号清单（review_json 规整后的 dict 列表）：`i. [type/kind] issue（改法）`。"""
    lines = []
    for i, item in enumerate(as_list(issues), 1):
        if not isinstance(item, dict):
            continue
        line = (f"{i}. [{as_text(item.get('type')) or 'unknown'}/"
                f"{as_text(item.get('fix_kind')) or 'rewrite'}] {as_text(item.get('issue'))}")
        suggestion = as_text(item.get("suggestion"))
        if suggestion:
            line += f"（改法：{suggestion}）"
        lines.append(line)
    return "\n".join(lines)


def _flavor_hits_text(flavor_hits) -> str:
    """去味目标编号清单（flavor_brief 同款行形态，不带上下文窗口）。

    接受 FlavorReport（取 targets()，跳过只报不改类）或 FlavorHit 列表。
    """
    if isinstance(flavor_hits, FlavorReport):
        targets = flavor_hits.targets()
    else:
        targets = [h for h in as_list(flavor_hits) if isinstance(h, FlavorHit)]
    lines = []
    for i, h in enumerate(targets, 1):
        quote = h.quote if len(h.quote) <= 40 else h.quote[:40] + "…"
        lines.append(f"{i}. 原文「{quote}」｜判定：{h.rule}——{h.analysis}｜处理：{h.fix}")
    return "\n".join(lines)


def _materials_lookup(materials: dict, key: str) -> str:
    return as_text((materials or {}).get(key))


# ================================================================ build_draft_context（seed 189-334 存活子集）

def _digest_vol(meta: dict, plot: dict) -> int | None:
    """本卷卷号：章 meta.plot_id「{vol}-{seq}」前缀优先，回退 plot.vol；无 → None。"""
    pid = as_text((meta or {}).get("plot_id")) or ""
    m = re.match(r"^(\d+)-", pid)
    if m:
        return int(m.group(1))
    vol = (plot or {}).get("vol")
    return vol if isinstance(vol, int) and not isinstance(vol, bool) else None


def chapter_memory_bundle(chapter_doc: ChapterDoc, beats: list, selected: dict,
                          mem, index):
    """本章 MemoryBundle：POV/点名/查询词推导 + ``build_memory_bundle``。

    终审 I-1b：拍循环内输入（meta/beats/selected/mem/index）完全不变，bundle 每章
    只建一次、逐拍共享——否则 ``_gather``+``_promise_made_map`` 每拍把全部章记忆各
    读两遍（O(N²) 文件读，终审实测全书 ≈31s 事件循环占用）。``build_draft_context``
    的 ``bundle=`` 参数传入即跳过内部重建（同输入同产物，五块语义零漂移）。
    """
    meta = chapter_doc.meta if chapter_doc is not None else {}
    cur_seq = getattr(chapter_doc, "seq", None) or meta.get("seq") or 0
    char_states = (mem.read_state().get("characters") or {})
    named = _chapter_cast_names(meta, beats)
    pov = (as_text(meta.get("pov")) or "").strip()
    if not pov:
        for b in as_list(beats):
            if isinstance(b, dict) and as_text(b.get("pov")):
                pov = as_text(b.get("pov")).strip()
                break
    cast_names = _cast_order(as_list((selected or {}).get("character_names")),
                             named, char_states)
    cast_names = _cast_offstage_filter(cast_names, named, char_states)
    query = " ".join(x for x in (as_text(meta.get("objective")),
                                 beats_text(meta, beats)) if x)
    return build_memory_bundle(mem=mem, index=index, pov=pov,
                               cast_names=list(cast_names),
                               cur_seq=cur_seq, query=query)


def build_draft_context(store: ProjectStore, chapter_doc: ChapterDoc, plot: dict, beats: list,
                        prev_tail: str, selected: dict, target_beat: str = "",
                        recent_draft: str = "", *, mem=None, index=None,
                        bundle=None, web_facts: str = "",
                        intent_block: str = "") -> DraftContext:
    """成稿上下文组装：system（base+方法论+story_block）+ user（core+droppable+task）。

    - system:`global.base_prompt` + `global.creation_method`（写作类必带，缺模板不炸）+
      `story_block(题材/基调/结局)`（README 项目设定，planning 复用）。
    - user 块序（seed build_draft_prompt 222-334）：core = 卷大纲(plot 概要)
      → 本章目标 → 本章细纲(beats_text) → 角色卡 → 视点经历档案 → 知识状态（严格遵守）；
      droppable = 最近正文(尾1500) → 世界观(selected 精确命中) → 世界现状 → 其他角色
      相关经历 → 相关历史事件 → 未回收承诺/伏笔提醒 → 前情提要(digest);
      task = task.draft_continue 渲染后清占位（带「只写这一拍」锁）。
    - P4 接线（``mem``/``index`` 均默认 None，旧调用全兼容）：``mem`` 给时
      （seed build_draft_prompt 的 memory 注入本地落地）——角色卡经
      ``_cast_offstage_filter``（dead/exited 且细纲未点名不出卡）+ ``_cast_order``
      （点名者最前，其余按 last_seen 降权），POV 卡附现状行；五块经
      ``build_memory_bundle`` 注入（未来过滤适用全部五块+digest，Review Focus #2）；
      ``rolling_summary`` = 本卷 digest 全文（DigestKeeper.read + 未来章段过滤）。
      ``mem=None`` 时五块返 ""、materials 维持 P3 五键；``bundle=`` 传入预建的
      本章 MemoryBundle（拍循环共享，终审 I-1b：每章只建一次，缺省内部补建）。
    - 预算裁剪(seed 293-311 逐字):droppable 按序累加超 `BUDGET_CHARS` 即丢;
      recent_draft 降级尾 3000 **不丢**;core+task 永不丢(任务指令带硬约束——
      「只写这一拍」裁掉会让模型失去长度约束冲到 ~2 万字，seed 真实事故)。
    - prev_tail 折进【最近正文】块头部(尾 `TAIL_PREV_CHAPTER`),与已写正文尾部
      (尾 `TAIL_BEAT`) 并列——seed 里对应物是 rolling_summary(P4 槽),本地降级为原文衔接。
    - ``intent_block``（P12）：本次**意图与范围**块（目标/前提/限制/正文范围，来自 brief）
      → 进 **core** 段（预算裁剪只丢 droppable → core 永不丢）。空串 → 不渲染该块
      （字节级零变化）。
    - ``web_facts``（P5 T10）：联网核查事实块（【现实参照】），**由调用方算好传入**——
      选段（BM25 + 读盘）是 CPU 密集活，调用方在 ``asyncio.to_thread`` 内算一次、
      每章一次（不是每拍一次）；本函数只拼块。缺省 "" → 不渲染该块。
    - materials 供 review/修正/deaify 复用:mem 给时八键（P3 五键 + related_events/
      open_promises/rolling_summary；knowledge_state 尾部带世界现状块供审核复用）。
    """
    loader = _loader(store)
    base = loader.get("global.base_prompt")
    system = _creative_system(loader, "task.draft_continue", base)
    story = _story_settings(store)
    block = story_block(story.get("genre", ""), story.get("tone", ""),
                        story.get("ending", ""))
    if block:
        system = f"{system}\n\n{block}" if system else block

    meta = chapter_doc.meta if chapter_doc is not None else {}
    selected = selected or {}
    cur_seq = getattr(chapter_doc, "seq", None) or meta.get("seq") or 0
    cast_sel_names = as_list(selected.get("character_names"))

    # ---------------- P4 接线：现状/点名/记忆五块/digest（mem=None 全跳过，旧行为逐字保留） ----------------
    char_states: dict = {}
    world_block = ""
    rolling = ""
    if mem is not None:
        state = mem.read_state()
        char_states = state.get("characters") or {}
        named = _chapter_cast_names(meta, beats)
        cast_sel_names = _cast_order(cast_sel_names, named, char_states)
        cast_sel_names = _cast_offstage_filter(cast_sel_names, named, char_states)
        if bundle is None:  # 未预建时内部补建（旧调用行为不变；拍循环应传预建共享件）
            bundle = chapter_memory_bundle(chapter_doc, beats, selected, mem, index)
        world_block = world_state_block(state)
        vol = _digest_vol(meta, plot)
        if vol is not None:
            rolling = filter_future_digest(DigestKeeper(mem).read(vol), cur_seq)
    else:
        bundle = None          # mem=None 时五块返 ""（bundle 实参也不生效，旧行为钉死）

    world_sel = world_entries_block(store, selected.get("world_titles"))
    cast_sel = cast_cards_block(store, cast_sel_names, states=char_states or None)

    tail = ""
    if prev_tail:
        tail += str(prev_tail)[-TAIL_PREV_CHAPTER:]
    if recent_draft:
        if tail:
            tail += "\n"
        tail += str(recent_draft)[-TAIL_BEAT:]

    # droppable 序 = 丢弃优先级（seed :224-248：世界现状/他人经历/事件/承诺/前情依次靠后保）
    droppable: list[tuple[str, str]] = [
        ("recent_draft", _fmt("【最近正文】", tail)),
        ("world_entries", _fmt("【世界观设定（检索命中）】", world_sel)),
        ("state_items", _fmt("【世界现状（**会变的状态**，以此为准）】", world_block)),
        ("other_char_events", _fmt("【其他角色相关经历】",
                                   bundle.other_char_events if bundle else "")),
        ("related_events", _fmt("【相关历史事件】",
                                bundle.related_events if bundle else "")),
        ("open_promises", _fmt("【未回收承诺/伏笔提醒】",
                               bundle.open_promises if bundle else "")),
        ("rolling_summary", _fmt("【前情提要】", rolling)),
        ("web_facts", _fmt("【现实参照】", web_facts)),
    ]
    knowledge_mat = bundle.knowledge_state if bundle else ""
    if bundle is not None and world_block:
        # 世界现状入 materials["knowledge_state"] 尾部（review/修正复用同键消费）
        knowledge_mat = (knowledge_mat + "\n" + world_block) if knowledge_mat \
            else world_block
    core: list[tuple[str, str]] = [
        ("arc_outline", _fmt("【当前卷大纲】", as_text((plot or {}).get("summary")))),
        # **本次意图与范围**（P12）：brief 的目标/前提/限制/正文范围 → 写作链**第一次**
        # 看得到「这本书要写什么、不能写什么、写到哪」（此前只有规划链与验收链吃 brief）。
        # 放 **core** 段：预算裁剪只丢 droppable → core **永不丢**（丢了等于没注入）。
        ("intent", _fmt("【本次意图与范围（来自需求解析）】", intent_block)),
        ("chapter_objective", _fmt("【本章目标】", as_text(meta.get("objective")))),
        ("remaining_beats", _fmt("【本章细纲】", beats_text(meta, beats, target_beat))),
        ("cast_cards", _fmt("【出场角色卡（**人物设定以此为准**；与细纲/前文冲突时以本卡为准）】",
                            cast_sel)),
        ("pov_memories", _fmt("【视点角色经历档案】",
                              bundle.pov_memories if bundle else "")),
        ("knowledge_state", _fmt("【知识状态（严格遵守）】", knowledge_mat)),
    ]

    # seed 同款：模板变量全传 ""（块由代码物理拼接），只填任务指令与章号
    task_text = loader.render(
        "task.draft_continue",
        chapter_seq=str(getattr(chapter_doc, "seq", None) or meta.get("seq") or "?"),
        style_guide=project_style_body(store), generation_profile="",
        arc_outline="", chapter_objective="", pov_character="", remaining_beats="",
        retrieved_entries="", cast_cards="", pov_memories="", knowledge_state="",
        related_events="", open_promises="", rolling_summary="", recent_draft="",
        target_beat=_task_instruction(target_beat),
    )
    task_block = _fmt("【任务】", _clean_placeholders(task_text))

    used = len(system) + sum(len(t) for _, t in core)
    kept: list[tuple[str, str]] = []
    for name, text in droppable:
        if used + len(text) > BUDGET_CHARS:
            if name == "recent_draft" and text:  # 旧正文降级保留尾部,不丢(seed 304-308)
                tail3k = _degrade_recent_draft(text)
                kept.append((name, tail3k))
                used += len(tail3k)
            break  # droppable 序即丢弃优先级:当前放不下,其后一律丢
        kept.append((name, text))
        used += len(text)
    # **任务指令不可丢**:带「只写这一拍」硬约束,裁掉会让模型失去长度约束冲到 ~2 万字
    # （seed 296-299 注释；seed 把 task 放在 chunks 尾部靠 break 前的特判保留——world 爆量
    # 时 break 会跳过 task，本实现把 task 提出循环强制保留，修正该顺序缺陷）。
    kept.append(("task", task_block))

    # 最终组装:core 必须在内(seed 320-323 修复:core 只参与预算计算会让细纲根本没注入)
    user = "\n\n".join(t for t in ([t for _, t in core] + [t for _, t in kept]) if t)
    materials = {
        "world_entries": world_sel,
        "cast_cards": cast_sel,
        "pov_memories": bundle.pov_memories if bundle else "",
        "knowledge_state": knowledge_mat,
        "outline": beats_text(meta, beats),
    }
    if bundle is not None:  # P4 五键激活（memory bundle 给时实填）
        materials["related_events"] = bundle.related_events
        materials["open_promises"] = bundle.open_promises
        materials["rolling_summary"] = rolling
    return DraftContext(system=system, user=user, materials=materials)


# ================================================================ task.context_select 与 review/修正/deaify 变量

def context_select_vars(chapter_meta: dict, beats: list, catalog_str: str,
                        target_beat: str = "") -> dict:
    """task.context_select 变量组装：章目标 + 细纲 + 目标节拍 + 资源目录。"""
    return {
        "objective": as_text((chapter_meta or {}).get("objective")),
        "beats": beats_text(chapter_meta or {}, beats),
        "target_beat": str(target_beat or ""),
        "catalog": catalog_str,
    }


def build_review_vars(materials: dict, chapter_seq, draft: str) -> dict:
    """task.draft_review 变量组装（materials 五键直取,章序转文本）。"""
    return {
        "chapter_seq": str(chapter_seq),
        "world_entries": _materials_lookup(materials, "world_entries"),
        "pov_memories": _materials_lookup(materials, "pov_memories"),
        "knowledge_state": _materials_lookup(materials, "knowledge_state"),
        "cast_cards": _materials_lookup(materials, "cast_cards"),
        "outline": _materials_lookup(materials, "outline"),
        "draft": draft or "",
    }


def build_revise_vars(materials: dict, issues, flavor_hits, draft: str) -> dict:
    """task.draft_revise 变量组装：审核意见编号清单 + 去味目标清单 + 材料 + 待修正正文。"""
    return {
        "issues": _issues_text(issues),
        "flavor_hits": _flavor_hits_text(flavor_hits),
        "outline": _materials_lookup(materials, "outline"),
        "world_entries": _materials_lookup(materials, "world_entries"),
        "cast_cards": _materials_lookup(materials, "cast_cards"),
        "pov_memories": _materials_lookup(materials, "pov_memories"),
        "knowledge_state": _materials_lookup(materials, "knowledge_state"),
        "draft": draft or "",
    }


def build_rewrite_beat_vars(materials: dict, beat_text: str, prev_text: str,
                            next_text: str, issues) -> dict:
    """task.draft_rewrite_beat 变量组装：细纲对齐基准 + 上/下拍衔接 + 本拍 + 意见清单。"""
    return {
        "outline": _materials_lookup(materials, "outline"),
        "prev_text": prev_text or "",
        "beat_text": beat_text or "",
        "next_text": next_text or "",
        "issues": _issues_text(issues),
    }


def build_deaify_vars(flavor_hits, draft: str, *, style_guide: str = "") -> dict:
    """task.draft_deaify 变量组装：风格指南（项目级 style skill，缺省空串）+ 命中清单 + 正文。

    ``style_guide`` 为可选关键字——不传时行为与从前逐字相同（空串）。
    ``style_sample`` 恒 ""（本地无粘贴样本链，设计 §2.6 明文可选）。
    """
    return {
        "style_guide": style_guide or "",
        "style_sample": "",  # 风格样本链（设计 §2.6 可选，本期不做）
        "flavor_hits": _flavor_hits_text(flavor_hits),
        "draft": draft or "",
    }


def build_deaify_edits_vars(targets_brief: str, *, style_guide: str = "") -> dict:
    """task.draft_deaify_edits 变量组装：定点目标清单(flavor_brief 产物) + 风格指南。

    ``style_guide`` 为可选关键字——不传时行为与从前逐字相同（空串）。
    """
    return {
        "style_guide": style_guide or "",
        "style_sample": "",  # 风格样本链（设计 §2.6 可选，本期不做）
        "targets": targets_brief or "",
    }
