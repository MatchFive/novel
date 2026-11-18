"""审计确定性规则纯函数层（P5 Task 1）：R1 数值单调 / R2 晚出场 / R3 知晓泄漏 / R4 时间锚。

全部纯函数：输入为 plain data（dict/list/tuple/str），零 I/O 零 LLM。
口径照计划裁定 2，候选式产出——finding 落 issues 后由人工 /issues 终裁。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 裁定 2 词表逐字：R1 跌落标记——(旧章, 新章] 区间内事件文本含任一词则数值回退不视为异常
DECLINE_MARKERS: tuple[str, ...] = (
    "跌落", "下降", "退化", "散功", "重伤",
    "被废", "降级", "削弱", "修为尽失", "武功全失",
)

# R2/R3 句子切分符；quote 截断长度
_SENT_SPLIT_RE = re.compile(r"[。！？!?\n]")
_QUOTE_MAX = 40

# R4 digest 时间锚
_ANCHOR_RE = re.compile(r"\[第(\d+)章\]")


@dataclass
class AuditFinding:
    """一条审计候选发现。rule ∈ "monotonic"/"offstage"/"knowledge"/"timeline"（LLM 段为
    "consistency_{type}"）；chapter 为关联章号（无则 None）；quote 为正文逐字证据。"""

    rule: str
    chapter: int | None
    title: str
    detail: str
    quote: str | None


def _sentence_of(body: str, term: str) -> str:
    """取含 term 的首个句子（按 。！？!?\\n 切句），超 40 字截 40；未命中返回 ""。"""
    for seg in _SENT_SPLIT_RE.split(body):
        if term in seg:
            return seg[:_QUOTE_MAX]
    return ""


def check_monotonic(state_updates: list[dict], events: list[dict]) -> list[AuditFinding]:
    """R1 数值单调：同角色同 field **相邻两次更新**（调用方按 chapter 升序传入），新旧值均
    int() 可解析且新<旧，且 (旧章, 新章] 区间内该角色无 event 文本含 DECLINE_MARKERS
    任一词 → 报数值回退。

    **同章的两条不是「相邻两次更新」**（2026-11-17 独立复审 P18-2）：同一章里同一角色
    同一字段出现两条（模型重复给出 / 手改）→ 那是**章内不一致**，不是跨章回退 →
    报「第2章为9，第2章回退为7」是**假回退**，噪音会淹没用户。
    → 同一 (角色, 字段, 章) **只留最后一条**（后写的算数的），
    且只在**章号严格变大**时才算相邻两次。
    """
    findings: list[AuditFinding] = []
    # **同 (角色, 字段, 章) 去重**（后写的覆盖先写的：章内多条以**最后一条**为准）
    # —— 这一步**同时**保证「相邻两次更新」是**不同章**的两次（去重后同一 (角色,字段)
    # 在每章至多一条，按章升序排列 → 相邻两条的章号必然**严格变大**）
    # → 同章两条是**章内不一致**，不是跨章回退（P21，独立复审 P18-2）。
    merged: dict[tuple[str, str, int], dict] = {}
    for upd in state_updates:
        merged[(upd["character"], upd["field"], upd["chapter"])] = upd
    ordered = sorted(merged.values(),
                      key=lambda u: (u["chapter"], u["character"], u["field"]))
    prev_by_key: dict[tuple[str, str], dict] = {}
    for upd in ordered:
        key = (upd["character"], upd["field"])
        prev = prev_by_key.get(key)
        prev_by_key[key] = upd
        if prev is None:
            continue
        old, new = _parse_int(prev["value"]), _parse_int(upd["value"])
        if old is None or new is None or new >= old:
            continue
        if _has_decline_event(events, upd["character"], prev["chapter"], upd["chapter"]):
            continue
        findings.append(AuditFinding(
            rule="monotonic",
            chapter=upd["chapter"],
            title="数值回退",
            detail=f"{upd['character']} 的 {upd['field']} 第 {prev['chapter']} 章为 {prev['value']}，"
                   f"第 {upd['chapter']} 章回退为 {upd['value']}，区间内无跌落标记事件",
            quote=f"第{upd['chapter']} {upd['character']} {upd['field']}：{prev['value']}→{upd['value']}",
        ))
    return findings


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_decline_event(events: list[dict], character: str, old_ch: int, new_ch: int) -> bool:
    """(旧章, 新章] 区间内该角色是否有 event 文本含跌落标记。"""
    for ev in events:
        if ev["character"] != character:
            continue
        if not (old_ch < ev["chapter"] <= new_ch):
            continue
        if any(marker in ev["text"] for marker in DECLINE_MARKERS):
            return True
    return False


def check_offstage(chapters: list[tuple[int, str]], offstage: list[dict]) -> list[AuditFinding]:
    """R2 死亡/下线晚出场：seq>退场章且章正文逐字含角色名或别名（len≥2）→ 每章每角色
    报一条，quote=含名句子。"""
    findings: list[AuditFinding] = []
    for seq, body in chapters:
        for entry in offstage:
            if seq <= entry["exit_chapter"]:
                continue
            names = [entry["name"]] + [a for a in (entry.get("aliases") or []) if len(a) >= 2]
            hit = next((n for n in names if n in body), None)
            if hit is None:
                continue
            findings.append(AuditFinding(
                rule="offstage",
                chapter=seq,
                title=f"{entry['status']}角色晚出场",
                detail=f"status={entry['status']} 的角色 {entry['name']} 第 {entry['exit_chapter']} 章退场，"
                       f"第 {seq} 章正文仍出现「{hit}」",
                quote=_sentence_of(body, hit),
            ))
    return findings


def check_knowledge(chapters: list[tuple[int, str, list[str]]], infos: list[dict]) -> list[AuditFinding]:
    """R3 知晓泄漏：章 cast 中角色 R ∉ 信息项 knows，且信息项术语（len≥2）逐字命中该章
    正文 → 每 (章, 信息项) 报一条，quote=含术语句子。"""
    findings: list[AuditFinding] = []
    for seq, body, cast_names in chapters:
        for info in infos:
            knows = info["knows"]
            unknowledgeable = [r for r in cast_names if r not in knows]
            if not unknowledgeable:
                continue
            term = next((t for t in info["terms"] if len(t) >= 2 and t in body), None)
            if term is None:
                continue
            findings.append(AuditFinding(
                rule="knowledge",
                chapter=seq,
                title="疑似知晓泄漏",
                detail=f"信息项「{info['title']}」知晓者 {knows}，cast 中 "
                       f"{'、'.join(unknowledgeable)} 不应知晓，正文却出现术语「{term}」",
                quote=_sentence_of(body, term),
            ))
    return findings


def check_digest_anchors(digest_text: str) -> list[AuditFinding]:
    """R4 时间锚（digest 侧）：文内 [第N章] 锚点序列须非降；倒挂 → 报，quote=倒挂行原文
    strip 后 ≤40 字。"""
    findings: list[AuditFinding] = []
    prev = 0
    for line in digest_text.splitlines():
        for m in _ANCHOR_RE.finditer(line):
            seq = int(m.group(1))
            if seq < prev:
                findings.append(AuditFinding(
                    rule="timeline",
                    chapter=seq,
                    title="时间锚倒挂",
                    detail=f"[第{seq}章] 早于前文锚点 [第{prev}章]",
                    quote=line.strip()[:_QUOTE_MAX],
                ))
            else:
                prev = seq
    return findings


def check_state_vs_events(state_rows: list[dict], event_chapters: dict[str, list[int]]) -> list[AuditFinding]:
    """R4 时间锚（state 侧）：state 角色 updated_chapter < 该角色事件最大章 → 报。"""
    findings: list[AuditFinding] = []
    for row in state_rows:
        chapters = event_chapters.get(row["character"])
        if not chapters:
            continue
        max_ch = max(chapters)
        if row["updated_chapter"] < max_ch:
            findings.append(AuditFinding(
                rule="timeline",
                chapter=row["updated_chapter"],
                title="现状章号倒挂",
                detail=f"{row['character']} 的 state 记至第 {row['updated_chapter']} 章，"
                       f"但事件已发生到第 {max_ch} 章",
                quote=f"state {row['character']} updated_chapter={row['updated_chapter']} "
                      f"< 事件最大章 {max_ch}",
            ))
    return findings
