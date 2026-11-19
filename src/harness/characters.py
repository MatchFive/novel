"""角色候选聚合 / 集合名词 / blocked_by / 角色卡起草 —— 「谁是主要角色、哪章被拦」的**判定唯一源**（spec X3）。

后续 Web API（Task 6/18/19）与写作门（``write_plot``）都从这里 import 判定结果，
各自**不得**再实现一套口径。纯判定模块：候选聚合无 LLM、无网络，只经 ``ProjectStore`` /
pathlib 读项目文件；唯一的 LLM 缝是 :func:`draft_character_card`（角色卡起草，Task 17）。

判定口径（全部固化在此，一处改动全站生效）：

- **beats 聚合**：遍历 ``store.list_chapters()`` 的 ``meta["units"]``，只认 dict 元素；
  ``characters`` 逐名 strip。``beats`` = 名字出现在多少个 units 里。
- **集合名词**（``COLLECTIVE_NAMES`` ∪ ``<root>/.harness/collective_names.txt``）从计数中
  **整体剔除**，连龙套都不算（他们不该被「建卡」追债）。显式名单，不用正则。
- **share = beats / 总 beats**；总 beats = **全部 units 数**（spec §4.6.1 字面读法）：
  每个 dict unit 都算一拍，**不管有没有 characters**——无角色名的拍计入分母，
  不计入任何角色的分子。总 beats = 0 时全体空表，不除零。
- **主角源**（spec 偏差，已拍板）：``role_type=="protagonist"`` 的角色卡 ∪ 用户维护的
  ``<root>/.harness/protagonists.txt``（每行一个名；# 注释与空行跳过；文件缺失按空）。
- **候选**：非主角、非集合名词、``share >= BEAT_SHARE_THRESHOLD``；其余具名个体是龙套。
- **blocked_by_chapter**：``{章 seq: 该章 units 里出现的无卡主要角色名}``，
  无卡 = 既无卡名也无 alias（见 ``carded_names``）；空 dict = 无章被拦。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from harness.llm.client import OpenAICompatClient
from harness.planning.pure import character_digest
from harness.prompts.loader import PromptLoader
from harness.run.events import EventLog
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.base import complete_structured, extract_json

__all__ = [
    "BEAT_SHARE_THRESHOLD",
    "COLLECTIVE_NAMES",
    "MAX_PROTAGONISTS",
    "PROFILE_KEYS",
    "ROLE_GUIDES",
    "CastAnalysis",
    "CastMember",
    "CastPlanError",
    "CharacterDraftError",
    "add_protagonist",
    "blocked_by_chapter",
    "carded_names",
    "collect_candidates",
    "draft_character_card",
    "generate_relationship_graph",
    "load_collective_names",
    "normalize_relations",
    "plan_cast",
    "reextract_cast",
    "remove_protagonist",
    "save_character_card",
]

log = logging.getLogger("characters")

# 具名个体 share 低于此值即龙套（spec §4.6.1）。
BEAT_SHARE_THRESHOLD: float = 0.03

# 默认集合名词表（spec §4.6.1）：显式名单，不用正则。
COLLECTIVE_NAMES: frozenset[str] = frozenset({
    "众新弟子",
    "围观少年与家长",
    "各峰执事",
    "到顶少年数人",
    "杂役同门数人",
})


def load_collective_names(root: Path) -> frozenset[str]:
    """默认表 ∪ ``<root>/.harness/collective_names.txt``（每行一个名，# 注释与空行跳过）。

    文件缺失 / 整文件坏编码 → 默认表兜底，不抛。
    """
    path = Path(root) / ".harness" / "collective_names.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return COLLECTIVE_NAMES
    extra = {line.strip() for line in text.split("\n")
             if line.strip() and not line.strip().startswith("#")}
    return COLLECTIVE_NAMES | extra


def _load_txt_names(root: Path, filename: str) -> list[str]:
    """``<root>/.harness/<filename>`` 每行一个名（# 注释与空行跳过）；缺失/坏 → 空。"""
    path = Path(root) / ".harness" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    names = []
    for line in text.split("\n"):
        name = line.strip()
        if name and not name.startswith("#") and name not in names:
            names.append(name)
    return names


@dataclass
class CastMember:
    """一个具名个体在全书 units 里的存在感统计。"""

    name: str
    beats: int            # 出现在多少个 units 里
    chapters: int
    first_chapter: int
    share: float          # beats / 总 beats


@dataclass
class CastAnalysis:
    """一次候选聚合的结果（collect_candidates 的返回类型）。"""

    protagonists: list[CastMember] = field(default_factory=list)
    candidates: list[CastMember] = field(default_factory=list)   # 非主角、非龙套（share ≥ 阈值）
    walk_ons: list[CastMember] = field(default_factory=list)     # 其余具名个体

    def major_names(self) -> list[str]:
        """主角 + 候选的名字——「主要角色」全集（blocked_by_chapter 的判定域）。"""
        return [m.name for m in self.protagonists] + [m.name for m in self.candidates]

    def uncarded_major(self, carded: set[str]) -> list[CastMember]:
        """主角+候选里**无卡**（卡名与 alias 都不在 carded）的成员，按 beats 降序。"""
        pool = list(self.protagonists) + list(self.candidates)
        out = [m for m in pool if m.name not in carded]
        out.sort(key=lambda m: (-m.beats, m.name))
        return out


def _iter_unit_characters(doc: ChapterDoc) -> list[str]:
    """一章 units 里的全部角色名（dict 元素才计数，逐名 strip、去空去重保序）。"""
    units = doc.meta.get("units")
    if not isinstance(units, list):
        return []
    names: list[str] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        chars = unit.get("characters")
        if not isinstance(chars, list):
            continue
        for raw in chars:
            name = str(raw).strip()
            if name and name not in names:
                names.append(name)
    return names


def collect_candidates(store: ProjectStore,
                       collective: frozenset[str] | None = None) -> CastAnalysis:
    """聚合 units 角色提及，按阈值/集合名词/主角源划分三档。

    ``collective`` 缺省 = ``load_collective_names(store.root)``（默认表 + 用户 override）。
    总 beats = 0（空项目）→ 三档全空，不除零。
    """
    if collective is None:
        collective = load_collective_names(store.root)
    # beats[名] = 出现的 unit 数；chapter_hits[名] = set(章 seq)
    beats: dict[str, int] = {}
    chapter_hits: dict[str, set[int]] = {}
    total_beats = 0
    for doc in store.list_chapters():
        units = doc.meta.get("units")
        if not isinstance(units, list):
            continue
        for unit in units:
            if not isinstance(unit, dict):
                continue
            # 总 beats = 全部 dict unit 数：无角色名的拍也计入分母（spec §4.6.1）
            total_beats += 1
            chars = unit.get("characters")
            if not isinstance(chars, list):
                continue
            seen: set[str] = set()
            for raw in chars:
                name = str(raw).strip()
                if not name or name in seen:  # 同一 unit 内同名只算一次
                    continue
                seen.add(name)
                beats[name] = beats.get(name, 0) + 1
                chapter_hits.setdefault(name, set()).add(doc.seq)

    def _member(name: str) -> CastMember:
        return CastMember(name=name, beats=beats[name],
                          chapters=len(chapter_hits[name]),
                          first_chapter=min(chapter_hits[name]),
                          share=beats[name] / total_beats)

    # 主角源：role_type==protagonist 的卡 ∪ protagonists.txt（spec 偏差，已拍板）
    pro_names: list[str] = []
    for card in store.list_characters():
        if card.get("role_type") == "protagonist" and card.get("name") not in pro_names:
            pro_names.append(str(card["name"]))
    for name in _load_txt_names(store.root, "protagonists.txt"):
        if name not in pro_names:
            pro_names.append(name)

    if total_beats == 0:
        return CastAnalysis()

    def _sort(members: list[CastMember]) -> list[CastMember]:
        members.sort(key=lambda m: (-m.beats, m.first_chapter, m.name))
        return members

    # units 里没提过的主角仍是主角（beats=0、first_chapter=0）——UI 要能展示完整主角源
    protagonists = _sort([_member(n) if n in beats
                          else CastMember(name=n, beats=0, chapters=0,
                                          first_chapter=0, share=0.0)
                          for n in pro_names])
    candidates: list[CastMember] = []
    walk_ons: list[CastMember] = []
    for name, n in beats.items():
        if name in collective:
            continue                      # 集合名词整体剔除，连龙套都不算
        if name in pro_names:
            continue                      # 主角已入 protagonists，不占候选/龙套坑
        (candidates if n / total_beats >= BEAT_SHARE_THRESHOLD
         else walk_ons).append(_member(name))
    return CastAnalysis(protagonists=protagonists,
                        candidates=_sort(candidates),
                        walk_ons=_sort(walk_ons))


def carded_names(store: ProjectStore) -> set[str]:
    """全部已建卡的名字 ∪ aliases——「无卡」判定的唯一口径。"""
    names: set[str] = set()
    for card in store.list_characters():
        name = str(card.get("name") or "").strip()
        if name:
            names.add(name)
        for alias in card.get("aliases") or []:
            alias = str(alias).strip()
            if alias:
                names.add(alias)
    return names


def blocked_by_chapter(store: ProjectStore, analysis: CastAnalysis | None = None
                       ) -> dict[int, list[str]]:
    """``{章 seq: 该章 units 里出现的无卡主要角色名}``；空 dict = 无章被拦。

    主要角色 = protagonists + candidates（``CastAnalysis.major_names``）；
    无卡 = 卡名与 alias 都不在 ``carded_names`` 里。值恒为 list，
    按该章 units 内首次出现顺序排列，只含至少一个名字的章。
    """
    if analysis is None:
        analysis = collect_candidates(store)
    carded = carded_names(store)
    major = set(analysis.major_names())
    blocked: dict[int, list[str]] = {}
    for doc in store.list_chapters():
        names = [n for n in _iter_unit_characters(doc)
                 if n in major and n not in carded]
        if names:
            blocked[doc.seq] = names
    return blocked


# ================================================================ 角色卡起草（Task 17）

#: profile 六键（§4.6.2 草稿 schema；Task 18 PUT body / Task 16 输出契约同形）。
PROFILE_KEYS: tuple[str, ...] = ("personality", "motivation", "background",
                                 "appearance", "speech_style", "ability")

#: role_type 词表（Q8）→ (role_label, role_guide)。guide 移植自 seed web_dream
#: autopilot_runner.py 的 ROLE_GUIDES（逐角色分类型生成的设计重心），键名按本地四键
#: 词表重映射：seed ``support`` 拆为本地 ``supporting``（剧情功能型）/ ``daily``
#: （日常烟火气），反派指南本地词表无对应键不挂（人设照需求写）。
ROLE_GUIDES: dict[str, tuple[str, str]] = {
    "protagonist": ("主角",
                    ("主角是**爽点与追读的担当**，设计重心：\n"
                    "- **过人之处压得住场**：一样读者服气的具体本事（专业、天赋或一手绝活），"
                    "这是他的立身之本，不是背景板设定；\n"
                    "- **魄力与目标感**：敢赌敢扛、关键时刻敢拍板，动机具体到人"
                    "（在乎的人/钱/面子/一口气）；\n"
                    "- **情绪是决策的一部分**：有火大、犯怂、嘴硬的时候，"
                    "不是永远冷静的面瘫；\n"
                    "- **外貌有记忆点**：身形/气度/标志物至少给一样，"
                    "不许「中等身量、像账房」式路人描写。")),
    "major": ("重要配角",
              ("重要配角是**主角的对照组**，设计重心：\n"
              "- **与主角同题不同解**：同样的处境/出身，选了相反的路——"
              "对照让主角更立得住；\n"
              "- **自己也是主角**：他有自己的目标与执念，不是为主角服务的工具人；\n"
              "- **关系有张力**：惺惺相惜/渐行渐远/亦敌亦友，"
              "至少给一层真实的情感结构。")),
    "supporting": ("次要配角",
                   ("次要配角是**剧情功能的承担者**，设计重心：\n"
                   "- **功能明确**：他服务哪个具体功能（帮手/信使/阻碍/见证人），"
                   "与主线的交接点写得出来；\n"
                   "- **不抢戏**：戏份与信息量克制，把舞台留给主角与重要配角；\n"
                   "- **有名有姓地活着**：一个职业习惯、一句口头禅、一样随身物件"
                   "就能立住，别写成纯报幕的 NPC。")),
    "daily": ("日常角色",
              ("日常角色是**烟火气与信息差的来源**，设计重心：\n"
              "- **功能明确**：他提供什么（消息/手艺/笑点/麻烦），不抢戏；\n"
              "- **具体而微**：一个职业习惯、一句口头禅、一样随身物件就能立住，"
              "不必铺开背景；\n"
              "- **有名有姓地活着**：给一个小欲望或小算盘，别写成纯报幕的 NPC。")),
}


# 主角上限（总设计附录 A.3 build_characters 契约）：全书最多两位主角。
MAX_PROTAGONISTS = 2


class CharacterDraftError(RuntimeError):
    """角色卡起草失败：args 带 complete_structured 的分类串（bad_json/empty/truncated/api:*）。

    分类串是 tools/base 钉死的观测词表（「供工具观测拼接」）——工具层把它拼进
    「失败（…）」观测，agent loop 的分类器据此判 failed/transient。
    """

    def __init__(self, reason: str, usage: object | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage


def _world_entries_text(store: ProjectStore) -> str:
    """【世界观要点】渲染：复用 planning.context 的 seed 移植口径（content 截 150、前 15 条）。

    跨模块引私有名与 tools/base 引 outline._extract_json_value 同一条纪律
    （copy-propagation：同一渲染口径不留两份）。
    """
    from harness.planning.context import _world_text

    return _world_text(store)


def _project_info_text(store: ProjectStore) -> str:
    """【项目与任务简报】：README 项目级创作设定（与 plan_plots_vars 的 instruction 同口径）。"""
    from harness.planning.context import story_settings
    from harness.projects import README_FIELDS

    story = story_settings(store)
    lines = [f"{label}：{story[key]}" for key, label in README_FIELDS if story.get(key)]
    return "\n".join(lines) if lines else "（未设定）"


def _existing_characters_text(store: ProjectStore, limit: int = 20) -> str:
    """【既有角色】渲染（模板格式「名字(定位｜别名：…)：背景摘要」）：character_digest 同口径。

    list_characters 只回 name/role_type/aliases，profile 逐卡回读（同 _cast_text 的
    容错口径：单卡坏不拖垮名册）；aliases 归一成顿号串（character_digest 吃 str）。
    """
    cards = []
    for card in store.list_characters():
        try:
            full = store.read_character(card["name"])
        except (FileNotFoundError, FileFormatError):
            full = {}
        profile = full.get("profile") if isinstance(full.get("profile"), dict) else {}
        cards.append({**card,
                      "aliases": "、".join(str(a) for a in (card.get("aliases") or []) if str(a).strip()),
                      "profile": profile})
    return character_digest(cards[:limit], 260)


def _first_dict(text: str):
    """extract：取首个**dict** JSON 值（数组/标量当解析失败——输出契约是单对象）。"""
    value = extract_json(text)
    return value if isinstance(value, dict) else None


def _normalize_card(raw: dict, *, name: str, role_type: str) -> dict:
    """规整成钉死 schema 的卡 dict（§4.6.2）：缺键补 ""、profile 强制 dict、
    aliases 强制 list[str]、name/role_type 强制入参值、陌生键丢弃。"""
    profile_src = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    profile = {}
    for key in PROFILE_KEYS:
        value = profile_src.get(key)
        profile[key] = value if isinstance(value, str) else ""
    aliases_raw = raw.get("aliases")
    if isinstance(aliases_raw, str):
        aliases = [a.strip() for a in aliases_raw.replace("，", ",").split(",") if a.strip()]
    elif isinstance(aliases_raw, list):
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
    else:
        aliases = []
    arc_raw = raw.get("arc")
    stages = arc_raw.get("stages") if isinstance(arc_raw, dict) else None
    return {"name": name, "role_type": role_type, "aliases": aliases,
            "profile": profile, "arc": {"stages": stages if isinstance(stages, list) else []}}


async def draft_character_card(*, store: ProjectStore, llm: OpenAICompatClient,
                               prompts: PromptLoader, settings, name: str,
                               role_type: str, requirement: str = "") -> dict:
    """渲染 task.auto_character_create → complete_structured → 规整成钉死 schema 的卡 dict。

    - ``settings`` 预留（路由按模板首行头 pro/0.5/8192 钉死，WRITING_ROUTING 不加键——
      P10 契约测试钉死路由表键数 19）；缺的变量/重试阶梯全在 tools/base。
    - 非法 ``role_type``（非 ROLE_GUIDES 四键）/ 空 ``name`` → ValueError（响亮失败，
      不渲染、不调 LLM）；LLM 侧失败 → :class:`CharacterDraftError`（带分类串）。
    - 规整口径逐字：缺键补 ""、profile 强制 dict（防模型回字符串）、name 强制入参名
      （模型不得改名）、aliases 强制 list[str]——下游 _cast_text/cast_roster
      硬依赖此形状（§4.6.2）。
    """
    del settings   # 路由钉死（模板首行头）；参数保留为调用契约的一部分
    name = str(name or "").strip()
    role_type = str(role_type or "").strip()
    if not name:
        raise ValueError("draft_character_card: name 不能为空")
    if role_type not in ROLE_GUIDES:
        raise ValueError(f"draft_character_card: 未知 role_type={role_type!r}"
                         f"（可用：{'/'.join(ROLE_GUIDES)}）")
    role_label, role_guide = ROLE_GUIDES[role_type]
    prompt = prompts.render(
        "task.auto_character_create",
        project_info=_project_info_text(store),
        world_entries=_world_entries_text(store),
        existing_characters=_existing_characters_text(store),
        role_type=role_type, role_label=role_label, role_guide=role_guide,
        requirement=str(requirement or ""), name=name)
    data, usage, reason = await complete_structured(
        llm, model=llm.provider.pro, temperature=0.5, max_tokens=8192,
        prompt=prompt, extract=_first_dict)
    if not isinstance(data, dict):
        raise CharacterDraftError(reason or "bad_json", usage=usage)
    return _normalize_card(data, name=name, role_type=role_type)


# ================================================================ 卡司规划（P24）

class CastPlanError(CharacterDraftError):
    """卡司规划失败：同 CharacterDraftError 的分类串词表（bad_json/empty/…）。"""


def _roster_entries(text: str) -> list[dict] | None:
    """extract：取 JSON **数组**，逐项校验（dict、name 非空、role_type ∈ ROLE_GUIDES），
    非法项丢弃；非数组/全非法 → None（complete_structured 据此走重试阶梯）。"""
    value = extract_json(text)
    if not isinstance(value, list):
        return None
    entries: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        role_type = str(item.get("role_type") or "").strip()
        if not name or role_type not in ROLE_GUIDES:
            continue
        requirement = item.get("requirement")
        entries.append({"name": name, "role_type": role_type,
                        "requirement": str(requirement).strip()
                                        if requirement is not None else ""})
    return entries if entries else None


async def plan_cast(*, store: ProjectStore, llm: OpenAICompatClient,
                    prompts: PromptLoader, settings) -> tuple[list[dict], object | None]:
    """渲染 task.auto_character_cast → complete_structured → 校验过的名册（去重保序）。

    - ``settings`` 预留（路由按模板首行头 pro/0.5/4096 钉死，同 draft_character_card
      的惯例——WRITING_ROUTING 不加键）；模板里告知模型主角剩余名额
      （= MAX_PROTAGONISTS − 既有 protagonist 数），超额由工具层降级兑现。
    - 解析失败/空名册 → :class:`CastPlanError`（分类串同 CharacterDraftError 词表，
      工具层拼进「失败（…）」观测，loop 分类器据此判 failed/transient）。
    """
    del settings   # 路由钉死（模板首行头）；参数保留为调用契约的一部分
    existing = store.list_characters()
    protagonist_slots = max(0, MAX_PROTAGONISTS
                            - sum(1 for c in existing if c.get("role_type") == "protagonist"))
    prompt = prompts.render(
        "task.auto_character_cast",
        project_info=_project_info_text(store),
        world_entries=_world_entries_text(store),
        existing_characters=_existing_characters_text(store),
        protagonist_slots=str(protagonist_slots))
    data, usage, reason = await complete_structured(
        llm, model=llm.provider.pro, temperature=0.5, max_tokens=4096,
        prompt=prompt, extract=_roster_entries)
    if not isinstance(data, list):
        raise CastPlanError(reason or "empty", usage=usage)
    roster: list[dict] = []
    seen: set[str] = set()
    for entry in data:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        roster.append(entry)
    return roster, usage


# ================================================================ 关系网（P33）

def normalize_relations(raw: object, resolve: dict[str, str]) -> tuple[list[dict], int]:
    """关系项规整（纯函数）：from/to 经 ``resolve`` 解析成规范卡名（name + alias → canonical）。

    丢弃并计数：非 dict 项 / 任一端未命中 / 解析后自配对 / relation 空白 / (from,to,
    relation) 精确三元重复。输出 dict 四键全 str（description 缺省 ""），保序。
    """
    items = raw if isinstance(raw, list) else []
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        from_name = resolve.get(str(item.get("from") or "").strip())
        to_name = resolve.get(str(item.get("to") or "").strip())
        relation = str(item.get("relation") or "").strip()
        if not from_name or not to_name or from_name == to_name or not relation:
            dropped += 1
            continue
        key = (from_name, to_name, relation)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append({"from": from_name, "to": to_name, "relation": relation,
                    "description": str(item.get("description") or "").strip()})
    return out, dropped


def _relations_extract(text: str):
    """extract：首个 dict 的 relations 数组（缺数组/空数组 → None 走重试阶梯）。"""
    value = extract_json(text)
    relations = value.get("relations") if isinstance(value, dict) else None
    if not isinstance(relations, list) or not relations:
        return None
    return relations


async def generate_relationship_graph(*, store: ProjectStore, llm: OpenAICompatClient,
                                      prompts: PromptLoader, settings,
                                      ) -> tuple[list[dict], object | None, int]:
    """渲染 task.character_relations → complete_structured → :func:`normalize_relations`。

    - ``settings`` 预留（路由钉死 standard/0.6/4096，seed router character_relations →
      STANDARD/0.6；同 plan_cast「模板首行头」惯例，WRITING_ROUTING 不加键）。
    - 名册 = ``cast_roster``（全卡 profile）+ ``list_characters``（aliases）；
      **全名册 <2 张卡 → ValueError**（seed 400「至少需要 2 个角色才能生成关系网」
      口径），零 LLM。调用方归「跳过/失败（参数）」观测。
    - 结构化失败 → :class:`CharacterDraftError`（同起草分类串词表）；非 LLMError 异常
      原样上抛（complete_structured 纪律），由调用方兜底归观测。
    - 返回 (relations, usage, dropped)：端点已解析成规范卡名；dropped = 丢弃计数。
    """
    del settings   # 路由钉死（模板首行头）；参数保留为调用契约的一部分
    from harness.memory.extract import cast_roster  # 惰性：顶层 import 成环（同 reextract_cast）

    roster = cast_roster(store)
    resolve: dict[str, str] = {}
    for card in store.list_characters():
        canonical = str(card.get("name") or "")
        if not canonical:
            continue
        resolve.setdefault(canonical, canonical)
        for alias in card.get("aliases") or []:
            resolve.setdefault(str(alias), canonical)
    if len(set(resolve.values())) < 2:
        raise ValueError("至少需要 2 个角色才能生成关系网")
    cast_cards = "\n".join(
        f"【{card['name']}】({card.get('role_type') or '角色'}) "
        f"{json.dumps(card.get('profile') or {}, ensure_ascii=False)}"
        for card in roster)
    prompt = prompts.render("task.character_relations", cast_cards=cast_cards)
    data, usage, reason = await complete_structured(
        llm, model=llm.provider.standard, temperature=0.6, max_tokens=4096,
        prompt=prompt, extract=_relations_extract)
    if not isinstance(data, list):
        raise CharacterDraftError(reason or "empty", usage=usage)
    relations, dropped = normalize_relations(data, resolve)
    return relations, usage, dropped


# ================================================================ 落卡（Task 18）

def add_protagonist(root: Path, name: str) -> None:
    """把名并入 ``<root>/.harness/protagonists.txt``——**protagonists.txt 的唯一写函数**
    （本文件的第二个写入方：protagonist 卡落盘时由 :func:`save_character_card` 调本函数；
    读侧是 ``collect_candidates`` 的 ``_load_txt_names``，写逻辑收拢此处，一处改动全站生效）。

    读侧同口径（# 注释/空行跳过、去重保序）；名已在名单 → 不动（幂等，重复落卡不追加）；
    文件缺失 → 新建。已有末行缺 ``\\n`` 时先补，防名与末行粘连。
    """
    name = str(name or "").strip()
    if not name or name in _load_txt_names(root, "protagonists.txt"):
        return
    path = Path(root) / ".harness" / "protagonists.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8")
        pad = "" if not text or text.endswith("\n") else "\n"
    except (OSError, UnicodeDecodeError):
        pad = ""
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{pad}{name}\n")


def remove_protagonist(root: Path, name: str) -> None:
    """把名滤出 ``<root>/.harness/protagonists.txt``——与 :func:`add_protagonist`
    读写收口同款（protagonists.txt 唯二写函数）。名不在 → 不动（幂等）；文件缺失 →
    不动；滤空后**保留空文件**（读侧容忍缺失，不删文件）。
    """
    name = str(name or "").strip()
    path = Path(root) / ".harness" / "protagonists.txt"
    if not name or not path.is_file():
        return
    before = _load_txt_names(root, "protagonists.txt")
    keep = [n for n in before if n != name]
    if keep == before:
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{n}\n" for n in keep))


def save_character_card(store: ProjectStore, events: EventLog, card: dict) -> Path:
    """落一张角色卡并广播 ``characters_built``：``store.upsert_character(card)`` →
    ``events.append("characters_built", {"count": 1, "names": [card["name"]]})`` → return path。

    ``role_type=="protagonist"`` 的卡落盘时名并入 ``<root>/.harness/protagonists.txt``
    （经 :func:`add_protagonist`，主角源两读一写）。payload 逐字钉死；「事件由调用方发」
    （§8.4）指 run 场景——run 内工具的落卡与 web 路由都经本函数，事件即在此发。
    """
    path = store.upsert_character(card)
    if card.get("role_type") == "protagonist":
        add_protagonist(store.root, str(card["name"]))
    events.append("characters_built", {"count": 1, "names": [card["name"]]})
    return path


async def reextract_cast(*, store: ProjectStore, mem, llm: OpenAICompatClient,
                         prompts: PromptLoader, settings) -> list[int]:
    """对已有章记忆逐章 ``reextract_chapter``（卡一落名册即生效，§8.4）；返回重提章号列表。

    名册 ``cast_roster(store)`` **现取一次、全章复用**——刚落的卡即刻进
    ``cast_cards`` 渲染；``list_chapters()`` 已定位到章的 doc 原样传 ``doc=``
    （不按 seq 再 glob：手写章文件名前缀与 fm 章号常不一致，Task 13 复审实证）。
    逐章幂等（先清后建）；失败的章跳过、不进返回列表（提取失败不阻断，P4 铁律），不 raise。
    """
    # 惰性 import 同 _world_entries_text：顶层 import 会经 memory.extract → writing 成环
    from harness.memory.extract import REEXTRACT_OK_TEXT, cast_roster, reextract_chapter

    def _warn(path: Path, exc: FileFormatError) -> None:
        log.warning("reextract_cast 跳过坏章: %s（%s）", path, exc)

    cards = cast_roster(store)
    done: list[int] = []
    for doc in store.list_chapters(on_error=_warn):
        result = await reextract_chapter(store=store, mem=mem, llm=llm, prompts=prompts,
                                         settings=settings, seq=doc.seq, doc=doc,
                                         cast_cards=cards)
        if result.text == REEXTRACT_OK_TEXT:
            done.append(doc.seq)
    return done
