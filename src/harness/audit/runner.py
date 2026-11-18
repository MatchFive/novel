"""审计编排（P5 Task 3）：确定性全量 + LLM 复核最近章 → supersede 落 issues。

流程（计划 Task 3 接口节逐字兑现）：

1. 读全部已定稿章（``meta.status == "done"``；坏章逐文件容错跳过 + log.warning）。
   cast 取 ``writing/context.py`` 的 ``_chapter_cast_names`` 口径（meta.characters +
   各节拍 pov，去括号注释/集合词，去重保序）；取不到则 cast=[]，R3 自然无候选；
2. 装配 R1-R4 输入（全部 plain data）：events/state_updates/state_rows/offstage 来自
   MemoryStore 章记忆与 read_state（**state_updates 取章记忆第 5 节「状态更新」的历史**
   ——state.md 只存最新值、没有多点历史时拿它兜底），infos 来自章记忆 info 段
   （knows=revealed_to，terms 确定性派生：标题 ≥2 字 + 内容《》/「」内 token ≥2 字），
   digest 读 DigestKeeper；
3. 章正文扫描段（五个 check 纯函数）整体 ``asyncio.to_thread``（P4 I-1 纪律：
   R2/R3 全章子串搜索 CPU 密集，不堵事件循环）；
4. findings 逐条 ``issues.add(source="audit", ..., chapter=finding.chapter)``；
   **supersede（裁定 5）**：每章首次落库前把该章 source=="audit" 的 pending 置
   resolved。同一次运行内确定性段先落、LLM 段后落，同章不重复 supersede——否则
   LLM 段会把本 run 刚落的确定性候选清掉（两段同 source，按章去重是本 run 口径）；
5. LLM 段：chapters 非 None 跑指定章（仅已定稿章），否则 seq 降序取最近 llm_recent
   （默认 12）章；逐章 review_chapter。失败语义（R1）：review 对 LLM 调用失败 raise
   LLMError、解析失败 raise ValueError——本层逐章 try/except 这两类，观测如实
   「LLM 复核失败：{exc}」（消息已过 reason_label 确定性映射，无瞬时词）并继续；
   确定性结果不因 LLM 失败丢失。raise 路径无 usage，失败章无 token 数据可记；
6. 落库举证门：LLM finding 的 quote 必须 ``locate_quote`` 命中该章正文（T2 已回验
   一次，落库前兜底再验——mock/未来改动防线），未命中丢弃计入「丢弃无证据 K 条」；
7. ``emit("audit_done", {"deterministic": n1, "llm": n2, "discarded": n3})``；
8. 返回观测文本：首行「一致性审计完成：确定性 N 条、LLM 复核 M 条（丢弃无证据 K 条），
   已落 issues」；零发现如实说（不谎称已落 issues）；LLM 失败章逐行列在首行后。

已知边界（报告注明）：**老项目**（章记忆是四层、没有「状态更新」节）的 R1 仍不产
生候选——它们没有多点数值历史（守卫位）；**新提取**的章记忆带第 5 节「状态更新」，
R1 拿得到「相邻两次更新」→ R1 会真实产出候选（2026-11-15 审核 D5 → 2026-11-17 P18
修复）；章的 findings 某轮清零时，该章旧 pending 候选不
被 supersede（裁定 5 字面：落库前才清），人工 /issues 终裁。
"""
from __future__ import annotations

import asyncio
import logging
import re

from harness.audit.review import review_chapter
from harness.audit.rules import (
    AuditFinding,
    check_digest_anchors,
    check_knowledge,
    check_monotonic,
    check_offstage,
    check_state_vs_events,
)
from harness.llm.client import LLMError
from harness.memory.digest import DigestKeeper
from harness.memory.inject import unresolved_promises_text
from harness.memory.store import MemoryStore
from harness.run.issues import IssuesStore
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore
from harness.text import as_list, as_text, locate_quote
from harness.writing.context import _chapter_cast_names

__all__ = ["run_audit"]

log = logging.getLogger("audit.runner")

# R1 参与数值比较的 state 字段（_parse_int 过滤非数值，境界名等天然跳过）
_STATE_VALUE_FIELDS = ("realm", "level", "location", "affiliation", "notes")
# R2 下线口径（同 writing/context.py _OFFSTAGE_STATUS：dead/exited；missing 不算下线）
_OFFSTAGE_STATUS = ("dead", "exited")
# info terms 派生：正文书名号《》/引号「」内 token
_QUOTED_TOKEN_RE = re.compile(r"《([^》]+)》|「([^」]+)」")
_VOL_NAME_RE = re.compile(r"^vol-(\d+)\.md$")

# LLM 复核注入块的确定性上限（seed _scan_context 口径的本地降级： memories 近 30 /
# knowledge [:10]；未回收承诺 [:10] 见 memory.inject.unresolved_promises_text；
# canonical_entries 无上限）
_MEMORIES_TOP_K = 30
_KNOWLEDGE_TOP_K = 10
_CAST_CARD_MAX = 300      # seed cast_cards 每角色 profile 截 300 字


# ------------------------------------------------------------------ 输入装配（纯函数）

def _info_terms(title: str, content: str) -> list[str]:
    """info 的 terms 确定性派生：标题（≥2 字）+ 内容《》/「」内 token（≥2 字），去重保序。"""
    terms: list[str] = []
    if len(title) >= 2:
        terms.append(title)
    for m in _QUOTED_TOKEN_RE.finditer(content or ""):
        token = m.group(1) or m.group(2)
        if token and len(token) >= 2 and token not in terms:
            terms.append(token)
    return terms


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _deterministic_findings(*, scanned: list[tuple[int, str, list[str]]],
                            datas: dict[int, dict], state: dict,
                            aliases: dict[str, list[str]],
                            digest_texts: list[str]) -> list[AuditFinding]:
    """R1-R4 五 check 的纯函数装配 + 执行（CPU 密集，调用方负责 to_thread）。

    ``scanned`` = [(seq, body, cast_names)]（已定稿章）；``datas`` = 章记忆 seq→解析
    dict；``state`` = read_state()；``aliases`` = 角色名册 name→aliases。
    """
    events: list[dict] = []
    event_chapters: dict[str, list[int]] = {}
    infos: list[dict] = []
    for seq in sorted(datas):
        data = datas[seq] or {}
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            text = f"{as_text(ev.get('title')) or ''}：{as_text(ev.get('summary')) or ''}"
            for name in ev.get("characters") or []:
                name = str(name)
                events.append({"character": name, "chapter": seq, "text": text})
                event_chapters.setdefault(name, []).append(seq)
        for info in data.get("info_updates") or []:
            if not isinstance(info, dict):
                continue
            title = (as_text(info.get("title")) or "").strip()
            if not title:
                continue
            infos.append({
                "title": title,
                "terms": _info_terms(title, as_text(info.get("content")) or ""),
                "knows": [str(x) for x in as_list(info.get("revealed_to")) if str(x)],
            })

    state_updates: list[dict] = []
    state_rows: list[dict] = []
    offstage: list[dict] = []
    # ① **章记忆第 5 层的历史**（P18，R1 复活）：state.md 只存最新值 → R1 恒空；
    #    章记忆按章保留「本章模型明确给出的字段」→ 这里拿到**相邻两次更新**。
    for seq in sorted(datas):
        for upd in (datas[seq] or {}).get("state_updates") or []:
            if not isinstance(upd, dict):
                continue
            character = (as_text(upd.get("character")) or "").strip()
            field = (as_text(upd.get("field")) or "").strip()
            value = as_text(upd.get("value"))
            if not (character and field) or not value:
                continue
            state_updates.append({"character": character, "field": field,
                                  "value": value, "chapter": seq})
    # ② state.md 兜底：**没有历史**的 (角色, 字段) 才补最新值一条
    #    （老项目的记忆是四层 → 本节不存在 → R1 行为与从前逐字不变）。
    seen_pairs = {(u["character"], u["field"]) for u in state_updates}
    for name, entry in ((state or {}).get("characters") or {}).items():
        entry = entry or {}
        updated = _as_int(entry.get("updated"))
        state_rows.append({"character": name, "updated_chapter": updated})
        for field in _STATE_VALUE_FIELDS:
            value = as_text(entry.get(field))
            if not value:
                continue
            if (name, field) in seen_pairs:
                continue    # 有历史 → 以历史为准（state.md 只是最新值的投影）
            state_updates.append({"character": name, "field": field,
                                  "value": value, "chapter": updated})
        status = (as_text(entry.get("status")) or "").strip()
        if status in _OFFSTAGE_STATUS:
            exit_chapter = _as_int(entry.get("last_seen"))
            if exit_chapter > 0:    # last_seen 缺失（0）无可靠退场章，不报防噪音
                offstage.append({"name": name, "status": status,
                                 "exit_chapter": exit_chapter,
                                 "aliases": aliases.get(name) or []})
    # check_monotonic 要求按 chapter 升序传入
    state_updates.sort(key=lambda u: u["chapter"])

    findings: list[AuditFinding] = []
    findings += check_monotonic(state_updates, events)
    findings += check_offstage([(seq, body) for seq, body, _cast in scanned], offstage)
    findings += check_knowledge(scanned, infos)
    for text in digest_texts:
        findings += check_digest_anchors(text)
    findings += check_state_vs_events(state_rows, event_chapters)
    return findings


# ---------------------------------------------------------- LLM 复核注入块（确定性渲染）

def _canonical_text(store: ProjectStore) -> str:
    """铁律设定槽：world 条目无上限（seed canonical_entries 口径）。"""
    lines = []
    for e in store.read_world():
        category = str(e.get("category") or "")
        title = str(e.get("title") or "")
        summary = as_text(e.get("summary")) or as_text(e.get("content")) or ""
        head = f"- [{category}] {title}" if category else f"- {title}"
        lines.append(f"{head}：{summary}" if summary else head)
    return "\n".join(lines)


def _cast_cards_text(store: ProjectStore, names: list[str]) -> str:
    """本章出场角色卡（seed 每角色截 300 字）；无卡/坏卡跳过不炸。"""
    lines = []
    for name in names:
        try:
            card = store.read_character(name)
        except (FileNotFoundError, FileFormatError):
            continue
        bits = [f"{k}：{as_text(v)}" for k, v in card.items()
                if k != "name" and as_text(v)]
        lines.append(f"- {name}：" + "；".join(bits)[:_CAST_CARD_MAX])
    return "\n".join(lines)


def _memories_text(datas: dict[int, dict], names: list[str], seq: int) -> str:
    """cast 角色经历档案：≤seq 章记忆 character_events，章号倒序近 30 条。"""
    wanted = set(names)
    lines = []
    for s in sorted(datas, reverse=True):
        if s > seq:
            continue
        for ce in datas[s].get("character_events") or []:
            if not isinstance(ce, dict) or (ce.get("character") or "") not in wanted:
                continue
            line = f"· [第{s}章] {as_text(ce.get('summary')) or ''}"
            quote = as_text(ce.get("quotes"))
            if quote:
                line += f"（台词：「{quote}」）"
            lines.append(line)
    return "\n".join(lines[:_MEMORIES_TOP_K])


def _knowledge_text(datas: dict[int, dict], seq: int) -> str:
    """知识状态槽：≤seq 知晓关系行 [:10]（渲染口径同 memory.inject ③）。"""
    lines = []
    for s in sorted(datas):
        if s > seq:
            continue
        for kn in datas[s].get("knowledge") or []:
            if not isinstance(kn, dict):
                continue
            lines.append(f"· {as_text(kn.get('info_title')) or ''}："
                         f"{as_text(kn.get('character')) or ''} 知道"
                         f"（第{_as_int(kn.get('chapter'))}章起）")
    return "\n".join(lines[:_KNOWLEDGE_TOP_K])


# ------------------------------------------------------------------ 落库（supersede）

def _supersede(issues: IssuesStore, chapter: int) -> None:
    """裁定 5：该章 source=="audit" 的 pending 全部置 resolved（新候选取代旧候选）。"""
    for old in issues.list("pending"):
        if old.source == "audit" and old.chapter == chapter:
            issues.set_status(old.id, "resolved")


# ------------------------------------------------------------------ 编排主入口

def _warn_broken(path, exc: FileFormatError) -> None:
    """坏章只降级当前章：log.warning 点名文件 + 原因，审计其余章照常。"""
    log.warning("audit 跳过坏章 %s: %s", path, exc.reason)


async def run_audit(*, store: ProjectStore, mem: MemoryStore, llm, prompts, settings,
                    chapters: list[int] | None, llm_recent: int = 12, emit) -> str:
    """一致性审计编排（见模块 docstring）。返回观测文本（首行计数逐字口径）。"""
    docs = [d for d in store.list_chapters(on_error=_warn_broken)
            if d.meta.get("status") == "done"]
    cast_by_seq = {d.seq: _chapter_cast_names(d.meta, as_list(d.meta.get("beats")))
                   for d in docs}
    datas: dict[int, dict] = {}
    for seq in mem.list_chapter_memories():
        data = mem.read_chapter_memory(seq)
        if data is not None:
            datas[seq] = data
    state = mem.read_state()
    aliases = {c["name"]: list(c.get("aliases") or []) for c in store.list_characters()}
    keeper = DigestKeeper(mem)
    digests_dir = mem.root / "memory" / "digests"
    digest_texts: list[str] = []
    if digests_dir.is_dir():
        for path in sorted(digests_dir.glob("vol-*.md")):
            m = _VOL_NAME_RE.match(path.name)
            if m:
                text = keeper.read(int(m.group(1)))
                if text:
                    digest_texts.append(text)

    # ③ 正文扫描段整体 to_thread（P4 I-1：R2/R3 全章子串搜索 CPU 密集）
    findings = await asyncio.to_thread(
        _deterministic_findings,
        scanned=[(d.seq, d.body, cast_by_seq[d.seq]) for d in docs],
        datas=datas, state=state, aliases=aliases, digest_texts=digest_texts)

    issues = IssuesStore(store.root)
    superseded: set[int] = set()    # 本 run 已 supersede 的章（确定性段先落，LLM 段不重复清）

    def _land(fs: list[AuditFinding]) -> int:
        n = 0
        for f in fs:
            if f.chapter is not None and f.chapter not in superseded:
                _supersede(issues, f.chapter)
                superseded.add(f.chapter)
            issues.add("audit", f.title, f.detail, f.quote, chapter=f.chapter)
            n += 1
        return n

    n1 = _land(findings)

    # ⑤ LLM 段：chapters 非 None 跑指定章（仅已定稿章），否则 seq 降序最近 llm_recent
    doc_by_seq = {d.seq: d for d in docs}
    if chapters is None:
        targets = sorted(doc_by_seq, reverse=True)[:llm_recent]
    else:
        targets = [s for s in dict.fromkeys(chapters) if s in doc_by_seq]
    canonical = _canonical_text(store)
    n2 = 0
    discarded = 0
    notes: list[str] = []
    for seq in targets:
        doc = doc_by_seq[seq]
        try:
            llm_findings, _usage = await review_chapter(
                llm=llm, prompts=prompts, chapter_seq=seq, draft_text=doc.body,
                canonical_entries=canonical,
                cast_cards=_cast_cards_text(store, cast_by_seq[seq]),
                character_memories=_memories_text(datas, cast_by_seq[seq], seq),
                knowledge_state=_knowledge_text(datas, seq),
                open_promises=unresolved_promises_text(datas, seq))
        except (LLMError, ValueError) as exc:
            # R1：review 失败 raise——观测如实（消息已过 reason_label 映射，无瞬时词），
            # 继续剩余章；确定性结果已落库，不丢。raise 路径无 usage 可记。
            notes.append(f"LLM 复核失败：{exc}")
            continue
        gated = []
        for f in llm_findings:
            if f.quote and locate_quote(doc.body, f.quote) is not None:
                gated.append(f)
            else:
                discarded += 1    # 落库举证门：quote 定位不到正文 = 无证据，丢弃
        n2 += _land(gated)

    if n1 == 0 and n2 == 0 and discarded == 0:
        first = "一致性审计完成：零发现（确定性 0 条、LLM 复核 0 条）——issues 无新增"
    elif n1 + n2 == 0:
        first = (f"一致性审计完成：确定性 {n1} 条、LLM 复核 {n2} 条"
                 f"（丢弃无证据 {discarded} 条）——候选均未过举证回验，issues 无新增")
    else:
        first = (f"一致性审计完成：确定性 {n1} 条、LLM 复核 {n2} 条"
                 f"（丢弃无证据 {discarded} 条），已落 issues")
    emit("audit_done", {"deterministic": n1, "llm": n2, "discarded": discarded})
    return "\n".join([first, *notes])
