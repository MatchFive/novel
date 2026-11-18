# 来源：语义移植自 web_dream（d:/project/web_dream/backend/app/services/memory_service.py，
# :53-133 提取主链 +:193-263 现状投影；行号锚点见 task-5-brief.md；判据不改，逐项偏离
# 见 task-5-report.md）。DB 仓储换 MemoryStore 文件投影（T4，唯一写盘方），提取模板
# 逐字移植 prompts.json:275-281。
"""章节记忆提取主链（P4 Task 5）：LLM 五键单次调用 → 解析 → 先清后建写章记忆 → 现状投影。

主链（seed extract 的文件化）：

1. 渲染 ``task.memory_extract``（cast_cards 名册 + 章节正文）→ complete_structured
   （WRITING_ROUTING 路由 standard/0.3/4096；扩额重试阶梯归 tools/base，**LLM 调用
   在 store 锁外**）；
2. 五键解析 + 三道写入前护栏：
   - 无名册角色过滤（裁定 1，seed :124-127 语义）：character_events 与
     state_updates.characters 中非名册角色名一律跳过——LLM 幻觉角色名不得进
     state.md（防永久污染）；events.characters / info_updates.revealed_to 同样
     只留名册名（seed char_ids/revealed 过滤的文件等价物）；
   - 写入侧消毒（裁定 2）：字段值含钉死分隔符（；/｜）的先替换为安全字符
     （; / |）再写盘——T4 评审实测 location="A；B" 会把 location 错档进
     time_marker；
   - 出场 seen 合成（裁定 3，seed :251-260 语义）：apply 前从 events.characters +
     character_events 合成「出场角色 seen 条目」（{"name": X}，其他字段不给），
     使模型没给现状的出场角色 last_seen 也推进（单调只增由 store 守卫）；
3. purge → write_chapter_memory（五层整文件重写）→ apply_state_updates（state.md
   投影，单调守卫/locked/合法 status 全归 store）——写盘串行，幂等由先清后建保证。
   **第 5 层「状态更新」**（P18，R1 复活）：``state_updates`` 投影进 ``state.md`` 后
   **历史就被丢弃了** → 审计的 R1（数值单调）恒空。→ 本层按章保留**模型明确给出的**
   角色状态字段（逐字段一条），给 R1 提供「相邻两次更新」。

失败不 raise：返回 "failed"（reason 经 harness.reasons 映射表进日志，禁瞬时词上屏），
由调用方如实观测「记忆提取失败」且不阻断章节成功。

统一提取入口 ``reextract_chapter``（P5 Task 4）：以**盘上正文**为准重提取一章记忆
（复用本模块主链的先清后建写盘顺序 ⇒ 幂等；任何异常收敛为失败文案，不 raise），
供写作链三处提取 hook 与工具层共用同一入口。

**定位到章的调用方**（审计改写链）传 ``doc=``：按 seq 再解析一次会落到**别的章**
（``read_chapter`` 按**文件名前缀** glob，章号却取自 front-matter——手写章两者常不一致）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from harness.llm.client import OpenAICompatClient
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader
from harness.reasons import reason_label
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import as_list, as_text
from harness.writing.pure import WRITING_ROUTING

if TYPE_CHECKING:   # 惰性 import 的类型标注（tools.base 与本模块成环，见 _extract_obj）
    from harness.tools.base import ToolResult, UsageMeter

__all__ = ["CHAPTER_BROKEN_LABEL", "CHAPTER_MISSING_LABEL", "REEXTRACT_FAIL_PREFIX",
           "REEXTRACT_OK_TEXT", "cast_cards_text", "cast_roster", "extract_chapter_memory",
           "reextract_chapter", "reextract_failed", "reextract_reason"]

log = logging.getLogger("memory.extract")


def _extract_obj(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 对象（五键结构校验交给解析层）。

    extract_json 惰性导入（tools.base 顶层 import outline、writing 顶层 import 本模块，
    本模块顶层再 import tools.base 会成环——同 base.extract_json 的惰性手法）。
    """
    from harness.tools.base import extract_json
    value = extract_json(text)
    return value if isinstance(value, dict) else None


def _sanitize(value):
    """写入侧消毒（裁定 2）：钉死分隔符；/｜换安全字符（; / |），深走 dict/list。"""
    if isinstance(value, str):
        return value.replace("；", ";").replace("｜", "|")
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    return value


def cast_cards_text(cast_cards: list[dict]) -> str:
    """名册注入文本（seed :69-71 逐字口径）：每行 ``【名】(定位) {json profile[:200]}``。

    无 profile dict 时 json 段为空串（行尾留一个空格，seed f-string 同款）；无名卡跳过。
    """
    lines = []
    for card in cast_cards or []:
        if not isinstance(card, dict):
            continue
        name = (as_text(card.get("name")) or "").strip()
        if not name:
            continue
        role = (as_text(card.get("role_type")) or "").strip() or "角色"
        profile = card.get("profile")
        profile_s = json.dumps(profile, ensure_ascii=False)[:200] \
            if isinstance(profile, dict) else ""
        lines.append(f"【{name}】({role}) {profile_s}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 五键解析（含裁定 1/2 护栏）

def _parse_events(raw, roster: set[str]) -> list[dict]:
    """全知事件：characters 只留名册名（seed char_ids 过滤的文件等价物）。"""
    out = []
    for ev in (x for x in as_list(raw) if isinstance(x, dict)):
        chars = [as_text(c).strip() for c in as_list(ev.get("characters"))]
        out.append({
            "title": as_text(ev.get("title")) or "",
            "summary": as_text(ev.get("summary")) or "",
            "location": as_text(ev.get("location")) or "",
            "time_marker": as_text(ev.get("time_marker")) or "",
            "characters": [c for c in chars if c and c in roster],
            "new_info": [as_text(x) or "" for x in as_list(ev.get("new_info"))],
            "foreshadow": as_text(ev.get("foreshadow")) or "",
        })
    return out


def _parse_character_events(raw, roster: set[str]) -> tuple[list[dict], list[str]]:
    """限知角色经历：名册外角色名跳过（seed :124-127 逐字语义）。返回 (经历, 跳过名)。"""
    out, skipped = [], []
    for ce in (x for x in as_list(raw) if isinstance(x, dict)):
        name = (as_text(ce.get("character")) or "").strip()
        if name not in roster:
            if name:
                skipped.append(name)
            continue
        out.append({
            "character": name,
            "summary": as_text(ce.get("summary")) or "",
            "quotes": as_text(ce.get("quotes")) or "",
            "acted": as_text(ce.get("acted")) or "",
            "felt": as_text(ce.get("felt")) or "",
            "got_lost": as_text(ce.get("got_lost")) or "",
            "secrets": as_text(ce.get("secrets")) or "",
        })
    return out, skipped


def _parse_info_updates(raw, roster: set[str]) -> list[dict]:
    """信息项：空 title 跳过；revealed_to 只留名册名（seed revealed 过滤）。"""
    out = []
    for iu in (x for x in as_list(raw) if isinstance(x, dict)):
        title = (as_text(iu.get("title")) or "").strip()
        if not title:
            continue
        revealed = [as_text(n).strip() for n in as_list(iu.get("revealed_to"))]
        out.append({"title": title, "content": as_text(iu.get("content")) or "",
                    "revealed_to": [n for n in revealed if n and n in roster]})
    return out


def _parse_promise_updates(raw) -> list[dict]:
    """承诺/伏笔：空 content 跳过；其余字段原样透传（kind/status 合法值校验归 store）。"""
    out = []
    for pu in (x for x in as_list(raw) if isinstance(x, dict)):
        content = (as_text(pu.get("content")) or "").strip()
        if not content:
            continue
        out.append({"kind": pu.get("kind"), "content": content,
                    "by_character": as_text(pu.get("by_character")) or "",
                    "status": as_text(pu.get("status")) or "",
                    "resolution": as_text(pu.get("resolution")) or "",
                    "due": pu.get("due")})
    return out


def _parse_state_updates(raw, roster: set[str]) -> tuple[dict, list[str]]:
    """运行时现状：characters 名册过滤（裁定 1——幻觉角色名不得进 state.md）；
    world 原样透传（(kind,key) upsert 与单调守卫归 store）。"""
    root = raw if isinstance(raw, dict) else {}
    chars, skipped = [], []
    for su in (x for x in as_list(root.get("characters")) if isinstance(x, dict)):
        name = (as_text(su.get("name")) or "").strip()
        if name not in roster:
            if name:
                skipped.append(name)
            continue
        entry = {"name": name}
        for key in ("realm", "level", "location", "affiliation", "status", "notes"):
            if su.get(key) not in (None, ""):
                entry[key] = su.get(key)
        chars.append(entry)
    return {"characters": chars,
            "world": [x for x in as_list(root.get("world")) if isinstance(x, dict)]}, skipped


#: 章记忆第 5 层「状态更新」记录的字段（= state_updates 的角色字段全集）
_STATE_LAYER_FIELDS = ("realm", "level", "location", "affiliation", "status", "notes")


def _state_layer(state_updates: dict) -> list[dict]:
    """章记忆第 5 层：把本章的**角色现状更新**摊成逐字段条目。

    - **只记模型明确给出的字段**（没给的不记——现状投影也是这个口径）；
    - **只记有名有姓的角色**（名册外的角色名早在解析层就被丢掉了）；
    - **合成的 seen 条目不记**（只有 name、没有字段 → 无内容可记）。
    """
    out: list[dict] = []
    for su in (state_updates.get("characters") or []):
        if not isinstance(su, dict):
            continue
        name = (as_text(su.get("name")) or "").strip()
        if not name:
            continue
        for field in _STATE_LAYER_FIELDS:
            value = su.get(field)
            if value in (None, ""):
                continue
            out.append({"character": name, "field": field, "value": as_text(value)})
    return out


# ---------------------------------------------------------------- 主链

async def extract_chapter_memory(*, mem: MemoryStore, llm: OpenAICompatClient,
                                 prompts: PromptLoader, settings, doc: ChapterDoc,
                                 cast_cards: list[dict],
                                 on_retry_emit=None, meter: UsageMeter | None = None,
                                 on_failed: Callable[[str], None] | None = None) -> str:
    """提取一章记忆并写盘，返回 "extracted" / "failed"（不 raise，见模块 docstring）。

    ``cast_cards`` 为全书名册（[{name, role_type, profile}]，调用方从 ProjectStore 组装）；
    ``on_retry_emit`` 预留的改写重试观测口（本轮无改写调用点，恒不使用）。
    ``meter`` 可选（P5 usage 入账）：把本次 LLM 调用的 usage 记进计量器；None=不记账，
    行为与从前逐字相同。``on_failed`` 可选：提取失败时回调**原始** reason 串
    （truncated/empty/bad_json/api:{kind}）——调用方据此映射确定性标签（不回调时
    调用方只拿到 "failed"）。
    LLM 调用在 store 锁外；purge/write/apply 三个写盘动作串行。
    """
    del on_retry_emit  # 预留口：本轮无消费方（签名随 brief 钉死）
    roster = {(as_text(c.get("name")) or "").strip()
              for c in (cast_cards or []) if isinstance(c, dict)}
    prompt = prompts.render("task.memory_extract",
                            cast_cards=cast_cards_text(cast_cards),
                            chapter_content=doc.body or "")
    provider = settings.providers.get(settings.default_provider) if settings else None
    if provider is None:
        log.warning("记忆提取失败（调用失败）：无 provider（default_provider=%r）",
                    getattr(settings, "default_provider", None))
        if on_failed is not None:
            on_failed(_NO_PROVIDER_REASON)
        return "failed"
    tier, temp, mt = WRITING_ROUTING["task.memory_extract"]
    model = getattr(provider, tier, None) or provider.standard

    # 惰性导入同 _extract_obj：顶层 import 会与 tools 包成环
    from harness.tools.base import complete_structured
    data, usage, reason = await complete_structured(
        llm, model=model, temperature=temp, max_tokens=mt,
        prompt=prompt, extract=_extract_obj)
    if meter is not None:
        meter.add(usage)   # usage 记账：响应未带 usage 时 meter.add(None) 跳过
    if data is None:
        log.warning("记忆提取失败（%s）：第%s章「%s」", reason_label(reason), doc.seq,
                    as_text((doc.meta or {}).get("title")))
        if on_failed is not None:
            on_failed(reason)
        return "failed"

    data = _sanitize(data)
    events = _parse_events(data.get("events"), roster)
    cevents, skipped_ce = _parse_character_events(data.get("character_events"), roster)
    infos = _parse_info_updates(data.get("info_updates"), roster)
    promises = _parse_promise_updates(data.get("promise_updates"))
    state_updates, skipped_su = _parse_state_updates(data.get("state_updates"), roster)
    if skipped_ce:
        log.warning("记忆提取：角色不在名册中，经历已跳过：%s", skipped_ce)
    if skipped_su:
        log.warning("记忆提取：角色不在名册中，现状已跳过：%s", skipped_su)

    # 裁定 3：出场 seen 合成（seed :251-260）——模型没给现状的出场角色 last_seen 也推进
    seen: list[str] = []
    for ev in events:
        seen.extend(ev["characters"])
    seen.extend(ce["character"] for ce in cevents)
    seen.extend(su["name"] for su in state_updates["characters"])
    named = {su["name"] for su in state_updates["characters"]}
    state_updates["characters"].extend(
        {"name": name} for name in dict.fromkeys(seen)
        if name in roster and name not in named)

    seq = doc.seq
    mem.purge_chapter(seq)
    mem.write_chapter_memory(seq, as_text((doc.meta or {}).get("title")),
                             {"events": events, "character_events": cevents,
                              "info_updates": infos, "promise_updates": promises,
                              "state_updates": _state_layer(state_updates)})
    mem.apply_state_updates(state_updates, seq)
    log.info("记忆提取：第%s章 → 事件 %d / 经历 %d / 信息 %d / 承诺 %d / 现状角色 %d"
             "（seen %d）/ 世界 %d", seq, len(events), len(cevents), len(infos),
             len(promises), len(named), len(state_updates["characters"]),
             len(state_updates["world"]))
    return "extracted"


# ---------------------------------------------------------------- 统一提取入口 reextract_chapter（P5 Task 4）

REEXTRACT_OK_TEXT = "记忆提取完成"
REEXTRACT_FAIL_PREFIX = "记忆提取失败："
CHAPTER_MISSING_LABEL = "章不存在"     # 确定性词，非 LLM reason
CHAPTER_BROKEN_LABEL = "章坏档"        # 确定性词，非 LLM reason

# 提取链里**非** LLM 分类串的失败（无 provider / 意外异常）：走 reason_label 的
# api:* 分支归「调用失败」——失败词表的唯一权威源是 harness.reasons.reason_label，
# 本仓不另抄一份（copy-propagation 纪律）。
_NO_PROVIDER_REASON = "api:no_provider"
_UNEXPECTED_REASON = "api:error"


def reextract_failed(text: str) -> bool:
    """统一提取入口的失败判定（**单一判定源**）：失败文案以 REEXTRACT_FAIL_PREFIX 开头。

    调用方（写作链三处提取 hook）一律走本函数判失败，不得另写一份字符串匹配。
    """
    return isinstance(text, str) and text.startswith(REEXTRACT_FAIL_PREFIX)


def reextract_reason(text: str) -> str:
    """失败文案的原因段（成功返回 ""）：取值必在确定性词表内。"""
    if not reextract_failed(text):
        return ""
    return text[len(REEXTRACT_FAIL_PREFIX):]


def cast_roster(store: ProjectStore) -> list[dict]:
    """全书角色名册（记忆提取的 cast_cards 源）：[{name, role_type, profile}]。

    profile 取角色卡 front-matter 的 profile 子 dict；无子 dict 时平铺标量键兜底
    （与 writing.context._card_profile 的 P0 KV 卡口径一致，list/dict 值不入 json 段）。
    单卡读坏（缺文件/坏 front-matter）不拖垮名册：该卡 profile 置空、名与定位照给。

    本函数是名册组装的**唯一权威源**（原在 tools/writing.py 的 _cast_roster，行为逐字
    迁入，调用方改 import——copy-propagation 纪律：两处拷贝会各自漂移）。
    """
    roster = []
    for card in store.list_characters():
        name = str(card.get("name") or "").strip()
        if not name:
            continue
        try:
            meta = store.read_character(name)
        except (FileNotFoundError, FileFormatError, OSError):
            meta = {}
        profile = meta.get("profile") if isinstance(meta.get("profile"), dict) else {
            key: value for key, value in meta.items()
            if key not in ("name", "aliases") and not isinstance(value, (dict, list))
        }
        roster.append({"name": name,
                       "role_type": str(card.get("role_type") or ""),
                       "profile": profile})
    return roster


async def reextract_chapter(*, store: ProjectStore, mem: MemoryStore,
                             llm: OpenAICompatClient, prompts: PromptLoader, settings,
                             seq: int, emit=None,
                             cast_cards: list[dict] | None = None,
                             doc: ChapterDoc | None = None) -> ToolResult:
    """统一提取入口：以**盘上正文**为准重提取一章记忆（幂等，不 raise）。

    - 正文来源 = ``store.read_chapter(seq)``（盘上正文为准：改写落盘后重提取提的就是
      改后的那一版）；章不存在 → 「记忆提取失败：章不存在」；front-matter 坏档
      （FileFormatError）→「记忆提取失败：章坏档」；
    - ``doc`` 可选关键字：**给了就用它**（不再按 seq 解析一次）——调用方已经**定位到**
      那一章时必须用它：``read_chapter(seq)`` 内部是**按文件名前缀** glob
      （``{seq:04d}-*.md``），而章号取自 front-matter——两者不一致时（手写章常见）
      按 seq 再解析一次会读到**别的章**（那一章的记忆被清掉重写，本章的记忆从不刷新）。
      **不传时行为与从前逐字不变**（既有调用点：``tools/memory.py`` 的
      ``extract_memory``、``writing.py`` 的三处 hook，都不传）；
    - 写盘顺序复用本模块主链（purge_chapter → write_chapter_memory →
      apply_state_updates），先清后建 ⇒ 同章重跑无重复条目（幂等）；
    - ``text`` = 「记忆提取完成」或「记忆提取失败：<原因>」；原因取确定性词表：
      LLM 失败原因经 ``harness.reasons.reason_label()`` 映射（禁瞬时词上屏），
      章级失败用上面两个确定性标签；
    - ``usage`` = 本次 LLM 调用的 usage（响应未带 usage 记 None）；
    - **不 raise**：任何异常收敛为失败文案——「提取失败不阻断」是 P4 铁律；
    - ``emit`` 预留观测口，**本轮无消费方**（写盘成功/失败均不发事件；同
      extract_chapter_memory 的 on_retry_emit 先例）；
    - ``cast_cards`` 为 None 时内部用 ``cast_roster(store)`` 构建；给了就用给的
      （写作链沿用「一次构建、全章复用」，避免每章重读角色卡）。
    """
    del emit  # 预留口：本轮无消费方（签名随 brief 钉死）
    # 惰性 import 同 _extract_obj：顶层 import 会与 tools 包成环
    from harness.tools.base import ToolResult, UsageMeter
    chapter = doc
    if chapter is None:
        try:
            chapter = store.read_chapter(seq)
        except FileNotFoundError:
            return ToolResult(text=f"{REEXTRACT_FAIL_PREFIX}{CHAPTER_MISSING_LABEL}", usage=None)
        except FileFormatError:
            return ToolResult(text=f"{REEXTRACT_FAIL_PREFIX}{CHAPTER_BROKEN_LABEL}", usage=None)
    cards = cast_cards if cast_cards is not None else cast_roster(store)
    meter = UsageMeter()
    reasons: list[str] = []
    try:
        status = await extract_chapter_memory(
            mem=mem, llm=llm, prompts=prompts, settings=settings, doc=chapter,
            cast_cards=cards, meter=meter, on_failed=reasons.append)
    except Exception:  # 提取失败不阻断：收敛为失败文案，绝不抛异常（异常已记日志）
        log.warning("重提取异常（第%s章），已按失败处理", seq, exc_info=True)
        return ToolResult(text=f"{REEXTRACT_FAIL_PREFIX}{reason_label(_UNEXPECTED_REASON)}",
                          usage=meter.total)
    if status == "extracted":
        return ToolResult(text=REEXTRACT_OK_TEXT, usage=meter.total)
    reason = reasons[0] if reasons else ""
    return ToolResult(text=f"{REEXTRACT_FAIL_PREFIX}{reason_label(reason)}", usage=meter.total)
