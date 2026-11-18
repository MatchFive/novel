# 注入端（P4 Task 7）：MemoryBundle 五块 + 时间锚点 + 未来过滤 + 现状行
"""记忆 → 提示词注入块的组装层，语义照 seed ``memory_injector.py``（行号锚点见
task-7-brief.md）与 ``assembler.py``（_OFFSTAGE_STATUS:110 / _state_line:124-149 /
_retrieve_states:550-563）。

与 seed 的差异（本地降级，均在报告注明）：
- seed 的 POV/他人/事件排序走 BM25 召回 + 语义重排（embedder）；本地注入端是
  **同步接缝**（build_draft_context 被同步测试与旧签名钉死），语义重排无 embedder
  可用，故走 ``terms_from_texts`` → ``bm25_rank`` → ``rank_candidates``
  （embedder=None，同 T3 Index.search 管线，仅去掉异步包装与倒排预筛）。
- seed 查询词来自章节关键词参数；本地签名（brief 钉死）无该参数，加 keyword-only
  ``query=""``——调用方（context.py）传 章目标+细纲 文本；无查询时一律走
  「最近 N 条」兜底（seed :136-139 同款零命中兜底）。
- related_events 语料取自 ``index.items`` 的 ``mem:`` 事件节（T3 隔离层的语料
  快照），seed 的 StoryEventRepository 对应物 = 章记忆的「## 事件」节。
- 承诺的「第N章埋」锚点：T4 解析产物 promise_updates 不含 made 章号（格式有、
  解析丢弃），本模块按钉死文件格式用正则从原文回收（不触碰 T4 解析契约）。

职责边界（裁定）：死亡过滤**只作用于角色卡组装层**（context.py）；本模块的
记忆区块不过滤死亡 POV/角色的经历。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.memory.store import MemoryStore
from harness.retrieval.bm25 import (
    DEFAULT_SETTINGS,
    bm25_rank,
    rank_candidates,
    terms_from_texts,
)
from harness.text import as_list, as_text

__all__ = [
    "MemoryBundle",
    "build_memory_bundle",
    "filter_future_digest",
    "state_line",
    "unresolved_promises_text",
    "world_state_block",
]

# 五块注入口径（seed 逐字）：POV top-8 / 他人每人 top-3（cast[:4]）/ knowledge [:10] /
# related_events top-3 / open_promises due≤cur+3 置顶 + [:6]。
_POV_TOP_K = 8
_OTHER_TOP_K = 3
_OTHER_CAST_MAX = 4
_KNOWLEDGE_TOP_K = 10
_RELATED_TOP_K = 3
_PROMISE_TOP_K = 6
_PROMISE_URGENT_WINDOW = 3
STATE_TOP_K = 8          # seed assembler.py:551 STATE_TOP_K

# 死亡/退场标签（seed assembler.py:110-112 逐字）；注入端自身不过滤，只负责文案。
_OFFSTAGE_LABELS = {"dead": "已死亡", "exited": "已退场", "missing": "失踪"}
# 已回收承诺状态（seed open_promises = status in ("open","pending") 的补集口径：
# 只有明确 resolved/closed/done 才算回收，其余——含空 status——都算未回收）
_RESOLVED_STATUS = frozenset({"resolved", "closed", "done", "fulfilled"})

# 章记忆「## 事件」节的条目行（T4 渲染格式钉死；只取 title/summary 两个渲染字段）
_EVENT_LINE = re.compile(r"^- \[全知\] (.*?) — (.*?)（")
# 承诺行的「第N章埋」锚点（回收 T4 解析丢弃的 made 章号）
_PROMISE_MADE_LINE = re.compile(
    r"^- \[(?P<kind>[^\]]*)\] (?P<content>.*?)——(?P<by>.*?)（第(?P<made>\d+)章埋")
# digest 的章段头（未来过滤用：章号 > cur_seq 的整段剔除）
_DIGEST_HEAD = re.compile(r"^### 第(\d+)章")


@dataclass
class MemoryBundle:
    """注入块五字段（seed memory_injector.py:39-50 字段名=draft_continue 模板变量）。"""

    pov_memories: str = ""
    other_char_events: str = ""
    knowledge_state: str = ""
    related_events: str = ""
    open_promises: str = ""
    hits: dict = field(default_factory=dict)  # 各区块命中数量（seed 同款，供观测）

    def any_hits(self) -> bool:
        return bool(self.pov_memories or self.other_char_events
                    or self.knowledge_state or self.related_events or self.open_promises)


@dataclass
class _Cand:
    """BM25 引擎内部包装（rank_candidates 要 int(id) 键，同 T3 _Entry）。"""

    id: int
    seq: int            # 源章号——时间锚点与兜底排序用
    payload: dict
    text: str


def _label(seq: int | None) -> str:
    """材料的时间标签（seed _label:83-88）：[第N章] / [时间未知]。"""
    if seq is None:
        return "[时间未知]"
    return f"[第{seq}章]"


def _event_text(ce: dict) -> str:
    """角色经历用于词面比较的文本（seed _event_text:250-255 逐字：汇总各字段）。"""
    return "；".join(x for x in (
        as_text(ce.get("summary")) or "", as_text(ce.get("quotes")) or "",
        as_text(ce.get("acted")) or "", as_text(ce.get("felt")) or "",
        as_text(ce.get("got_lost")) or "", as_text(ce.get("secrets")) or "",
    ) if x)


def _format_event_line(ce: dict) -> str:
    """角色经历渲染行（seed _format_event_line:263-275 逐字字段序）。"""
    parts = [as_text(ce.get("summary")) or ""]
    if ce.get("quotes"):
        parts.append(f"台词：「{ce['quotes']}」")
    if ce.get("acted"):
        parts.append(f"作为：{ce['acted']}")
    if ce.get("felt"):
        parts.append(f"感受：{ce['felt']}")
    if ce.get("got_lost"):
        parts.append(f"得失：{ce['got_lost']}")
    if ce.get("secrets"):
        parts.append(f"秘密：{ce['secrets']}")
    return "；".join(p for p in parts if p)


def _rank_or_none(cands: list[_Cand], terms: list[str], query: str, k: int,
                  cfg) -> list[_Cand]:
    """BM25 召回 + 相关性门槛排序（seed :123-135 管线的本地同步降级）。

    返回 top-k；无查询词/零命中返回 []（调用方走「最近 N 条」兜底）。
    """
    if not terms or not cands:
        return []
    pool, scores = bm25_rank(cands, terms, lambda c: c.text, limit=cfg.recall_k)
    if not pool:
        return []
    picked, _ranked = rank_candidates(
        pool=pool, scores=scores, query=query, text_of=lambda c: c.text,
        cfg=cfg, top_k=k, embedder=None, terms=terms)
    return list(picked)


def _recent(cands: list[_Cand], k: int) -> list[_Cand]:
    """零命中兜底：最近 N 条（seed :136-139 注释——recency 是合理的相关性代理）。

    章号降序、同章保持文件序（稳定排序）；候选本身按章升序收集，故直接逆序取前 k。
    """
    return list(reversed(cands))[:k] if cands else []


def _gather(mem: MemoryStore, cur_seq: int) -> dict[int, dict]:
    """全部章记忆的解析结果（**未来过滤**：章号 > cur_seq 一律不读入，seed :90-97）。"""
    out: dict[int, dict] = {}
    for seq in mem.list_chapter_memories():
        if seq > cur_seq:
            continue
        data = mem.read_chapter_memory(seq)
        if data is not None:
            out[seq] = data
    return out


def _promise_made_map(mem: MemoryStore, datas: dict[int, dict]) -> dict[tuple, int]:
    """回收承诺的「第N章埋」章号：T4 解析产物不含 made（格式有、解析丢弃），
    按钉死文件格式从原文回收，键 (kind, content, by) → made。"""
    made: dict[tuple, int] = {}
    for seq in datas:
        path = mem.root / "memory" / f"{seq:04d}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            m = _PROMISE_MADE_LINE.match(line.strip())
            if m:
                made[(m.group("kind"), m.group("content"), m.group("by"))] = \
                    int(m.group("made"))
    return made


def build_memory_bundle(*, mem: MemoryStore, index, pov: str,
                        cast_names: list[str], cur_seq: int,
                        cfg=DEFAULT_SETTINGS, query: str = "") -> MemoryBundle:
    """照 seed memory_injector 组装五块注入文本（空数据安全返回）。

    - 未来过滤（Review Focus #2）：章号 > cur_seq 的材料一律剔除（全部五块）。
    - 时间锚点 `[第N章]`（seed _label:83）；无查询/零命中 → 最近 N 条兜底。
    - related_events 语料 = ``index.items`` 的 ``mem:`` 事件节（index=None → 空块）。
    - **不过滤死亡 POV/角色的经历**（裁定：死亡过滤只在角色卡组装层）。
    """
    bundle = MemoryBundle()
    pov = (pov or "").strip()
    query = (query or "").strip()
    terms = terms_from_texts([query], max_terms=16) if query else []
    datas = _gather(mem, cur_seq)

    # ① POV 角色经历档案：top-8（零命中兜底最近 8 条；seed :119-141）
    if pov:
        # **候选 id 必须全局唯一**（2026-11-15 独立审核 M5）：原先用的是
        # ``enumerate(datas.items())`` 的**章下标** → 同一章的多条经历共用一个 id →
        # BM25 的 ``{id: score}`` 字典里后者**覆盖**前者，rrf 还把它**累加 N 次**
        # → top-8 全被一章占满、别的章被挤出。→ 用**展平后**的下标（④ 早就是全局计数器）。
        flat_pov = [(seq, ce) for seq, data in datas.items()
                     for ce in data.get("character_events") or []
                     if isinstance(ce, dict) and (ce.get("character") or "") == pov]
        cands = [_Cand(i, seq, ce, _event_text(ce))
                 for i, (seq, ce) in enumerate(flat_pov)]
        picked = _rank_or_none(cands, terms, query, _POV_TOP_K, cfg) \
            or _recent(cands, _POV_TOP_K)
        bundle.pov_memories = "\n".join(
            f"· {_label(c.seq)} {_format_event_line(c.payload)}" for c in picked)
        bundle.hits["pov_memories"] = len(picked)

    # ② 其他角色相关经历：每人 top-3（cast[:4]；seed :146-172）
    others = [str(n).strip() for n in as_list(cast_names) if str(n).strip() and
              str(n).strip() != pov]
    if others:
        lines: list[str] = []
        for name in others[:_OTHER_CAST_MAX]:
            flat_name = [(seq, ce) for seq, data in datas.items()
                         for ce in data.get("character_events") or []
                         if isinstance(ce, dict) and (ce.get("character") or "") == name]
            cands = [_Cand(i, seq, ce, _event_text(ce))
                     for i, (seq, ce) in enumerate(flat_name)]
            picked = _rank_or_none(cands, terms, query, _OTHER_TOP_K, cfg) \
                or _recent(cands, _OTHER_TOP_K)
            for c in picked:
                lines.append(f"· {_label(c.seq)} {_format_event_line(c.payload)}")
        bundle.other_char_events = "\n".join(lines)
        bundle.hits["other_char_events"] = len(lines)

    # ③ 知识状态：POV 知晓行 [:10]（seed :175-191；本地只存「知道」关系，
    #    渲染 `· 标题：角色 知道（第N章起）`——「不知道」本地无语料，报告注明）
    if pov:
        knows = [k for data in datas.values()
                 for k in (data.get("knowledge") or [])
                 if isinstance(k, dict) and (k.get("character") or "") == pov]
        lines = []
        for k in knows[:_KNOWLEDGE_TOP_K]:
            when = f"（第{k['chapter']}章起）" if k.get("chapter") else ""
            lines.append(f"· {as_text(k.get('info_title')) or ''}：{pov} 知道{when}")
        bundle.knowledge_state = "\n".join(lines)
        bundle.hits["knowledge_state"] = len(lines)

    # ④ 相关历史事件：mem: 事件节 BM25 top-3（seed :194-214；零命中不注入，无兜底）
    if index is not None and terms:
        cands: list[_Cand] = []
        n = 0
        for it in index.items:
            parts = str(it.id).split(":")
            if len(parts) < 4 or parts[0] != "mem" or parts[2] != "事件":
                continue
            try:
                seq = int(parts[1])
            except ValueError:
                continue
            if seq > cur_seq:  # 未来过滤（语料可能含未来章记忆）
                continue
            for line in str(it.text).splitlines():
                m = _EVENT_LINE.match(line.strip())
                if not m:
                    continue
                n += 1
                title, summary = m.group(1), m.group(2)
                cands.append(_Cand(n, seq, {"title": title, "summary": summary},
                                   f"{title}：{summary}"))
        picked = _rank_or_none(cands, terms, query, _RELATED_TOP_K, cfg)
        if picked:
            bundle.related_events = "\n".join(
                f"· {_label(c.seq)} "
                f"{as_text(c.payload.get('summary')) or as_text(c.payload.get('title')) or ''}"
                for c in picked)
            bundle.hits["related_events"] = len(picked)

    # ⑤ 未回收承诺/伏笔提醒：due≤cur+3 置顶（标注已到期/临近到期），其余 made 倒序，
    #    [:6] + 溢出提示行（seed :219-245 逐字口径）
    made_map = _promise_made_map(mem, datas)
    promises: list[dict] = []
    for seq, data in datas.items():
        for pr in data.get("promise_updates") or []:
            if not isinstance(pr, dict):
                continue
            status = (as_text(pr.get("status")) or "").strip()
            if status in _RESOLVED_STATUS:
                continue    # 已回收不进未回收块（seed open_promises 口径）
            kind = as_text(pr.get("kind")) or "promise"
            content = as_text(pr.get("content")) or ""
            by = as_text(pr.get("by_character")) or ""
            made = made_map.get((kind, content, by), seq)  # 原文缺失 → 该文件章号
            due = pr.get("due")
            try:
                due = int(due) if due not in (None, "") else None
            except (TypeError, ValueError):
                due = None
            promises.append({"kind": kind, "content": content, "by": by,
                             "made": made, "due": due})
    if promises:
        def _sort_key(pr: dict) -> tuple[int, int, int]:
            urgent = bool(pr["due"]) and pr["due"] <= cur_seq + _PROMISE_URGENT_WINDOW
            return (0 if urgent else 1,
                    pr["due"] if (urgent and pr["due"]) else 0,
                    -(pr["made"] or 0))

        promises.sort(key=_sort_key)
        lines = []
        for pr in promises[:_PROMISE_TOP_K]:
            marker = ("承诺" if pr["kind"] == "promise"
                      else ("伏笔" if pr["kind"] == "foreshadow" else "悬念"))
            origin = f"第{pr['made']}章埋" if pr["made"] else "时间未知"
            due = ""
            if pr["due"]:
                if pr["due"] <= cur_seq:
                    due = f"，已到期（计划第{pr['due']}章回收）"
                elif pr["due"] <= cur_seq + _PROMISE_URGENT_WINDOW:
                    due = f"，临近到期（计划第{pr['due']}章回收）"
                else:
                    due = f"，计划第{pr['due']}章回收"
            lines.append(f"· [{marker}｜{origin}{due}] {pr['content']}")
        if len(promises) > _PROMISE_TOP_K:
            lines.append(f"· …共 {len(promises)} 条未兑现，详见记忆库")
        bundle.open_promises = "\n".join(lines)
        bundle.hits["open_promises"] = len(promises)

    return bundle


# ================================================================ 未兑现承诺槽（共享口径）

# 未回收承诺槽的条数上限（P5 审计口径：章号倒序 [:10]）
_UNRESOLVED_TOP_K = 10


def unresolved_promises_text(datas: dict[int, dict], seq: int, *,
                             top_k: int = _UNRESOLVED_TOP_K) -> str:
    """未回收承诺槽：≤seq 且 status 未明确回收的 promise_updates，章号倒序 [:10]。

    **本仓唯一实现**（copy-propagation 纪律）：P5 审计（``audit/runner.py`` 的
    LLM 复核注入块）与 P6 验收（``check/context.py`` 的 open_promises 槽）都从这里
    取——同一口径不许留两份。

    「未回收」= status 不在 ``_RESOLVED_STATUS`` 里（**只有明确** resolved/closed/
    done/fulfilled 才算回收，其余——含空 status——都算未回收）。
    渲染行：``· [kind] content——by_character（第N章埋）``。
    """
    lines = []
    for s in sorted(datas, reverse=True):
        if s > seq:
            continue
        for pr in (datas.get(s) or {}).get("promise_updates") or []:
            if not isinstance(pr, dict):
                continue
            if (as_text(pr.get("status")) or "").strip() in _RESOLVED_STATUS:
                continue
            lines.append(f"· [{as_text(pr.get('kind')) or 'promise'}] "
                         f"{as_text(pr.get('content')) or ''}——"
                         f"{as_text(pr.get('by_character')) or ''}（第{s}章埋）")
    return "\n".join(lines[:top_k])


# ================================================================ 现状行 / 世界现状块
# （seed assembler.py 移植；死亡过滤本身在 context.py 角色卡组装层执行）

def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def state_line(name: str, state: dict) -> str:
    """角色**运行时现状**行（seed _state_line:124-149 逐字移植）。

    只在有可展示字段时返回；字段全空（如只有 ``last_seen``）→ 空串（不注入）。
    """
    st = state if isinstance(state, dict) else {}
    bits: list[str] = []
    realm = st.get("realm")
    level = st.get("level")
    if realm or (level is not None and str(level).strip() != ""):
        head = str(realm).strip() if realm else ""
        if level is not None and str(level).strip() != "":
            head = (head + " " if head else "") + f"Lv{level}"
        bits.append(f"修为/等级：{head}")
    if st.get("location"):
        bits.append(f"所在：{st['location']}")
    if st.get("affiliation"):
        bits.append(f"阵营：{st['affiliation']}")
    if st.get("notes"):
        bits.append(str(st["notes"]))
    label = _OFFSTAGE_LABELS.get((as_text(st.get("status")) or "").strip())
    if label:
        bits.append(f"状态：{label}")
    if not bits:
        return ""
    return "  【当前状态（实际，以此为准）】" + "；".join(bits)


def world_state_block(state: dict, top_k: int = STATE_TOP_K) -> str:
    """世界对象现状块（seed _retrieve_states:550-563 逐字移植）：领地/势力/资源等
    **会变的状态**按 importance 取 top-k，与静态 lore 分开。无条目 → 空串。"""
    world = (state or {}).get("world") or {}
    rows = sorted(world.values(),
                  key=lambda e: _as_int((e or {}).get("importance"), 3),
                  reverse=True)[:top_k]
    lines: list[str] = []
    for s in rows:
        s = s or {}
        name = (as_text(s.get("label")) or as_text(s.get("key")) or "").strip()
        kind = (as_text(s.get("kind")) or "other").strip() or "other"
        val = (as_text(s.get("value")) or "").strip()
        lines.append(f"- [{kind}] {name}：{val}" if val else f"- [{kind}] {name}")
    return "\n".join(lines)


# ================================================================ digest 未来过滤

def _filter_section(sec: list[str], cur_seq: int) -> list[str] | None:
    """单情节节过滤：有幸存章段（或根本无章段，格式外容错）→ 节头+幸存内容；
    整节章段全未来 → None（连节头一起丢，M-b——未来情节的标题也是未来信息）。"""
    body: list[str] = []
    chapters = survived = 0
    drop = False
    for line in sec[1:]:
        m = _DIGEST_HEAD.match(line.strip())
        if m:
            chapters += 1
            drop = int(m.group(1)) > cur_seq
            if not drop:
                survived += 1
                body.append(line)
            continue
        if line.strip().startswith("#"):
            drop = False
            body.append(line)
            continue
        if not drop:
            body.append(line)
    if chapters and not survived:
        return None
    return [sec[0]] + body


def filter_future_digest(text: str, cur_seq: int) -> str:
    """剔除 digest 里章号 > cur_seq 的 ``### 第N章`` 段（Review Focus #2：

    未来章的「前情」尚未发生，不得当作既成事实注入）。卷头（首个 ``## `` 之前）
    按章段过滤直接保留；情节节按 ``## `` 切分后逐节过滤——整节无幸存章段时连
    「## 情节 N. title」节头一起丢（M-b：乱序写作时未来情节标题不得泄漏进提示词）。
    全未来 → 空串。
    """
    head: list[str] = []
    sections: list[list[str]] = []
    cur: list[str] | None = None
    for line in (text or "").splitlines():
        if line.strip().startswith("## "):
            cur = [line]
            sections.append(cur)
        elif cur is None:
            head.append(line)
        else:
            cur.append(line)
    # 卷头区：无节头语义，按章段过滤直接保留
    out: list[str] = []
    drop = False
    for line in head:
        m = _DIGEST_HEAD.match(line.strip())
        if m:
            drop = int(m.group(1)) > cur_seq
            if not drop:
                out.append(line)
            continue
        if line.strip().startswith("#"):
            drop = False
        if not drop:
            out.append(line)
    for sec in sections:
        kept = _filter_section(sec, cur_seq)
        if kept is not None:
            out.extend(kept)
    return "\n".join(out).strip()
