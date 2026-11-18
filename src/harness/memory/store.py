# MemoryStore：记忆文件读写（P4 Task 4，格式钉死、T3 build_corpus 已消费）
"""章记忆与运行时现状的**文件投影**（seed ``memory_service`` 的文件化）。

两种文件（格式以计划钉死为准，本模块是唯一的写盘方与权威读方）：

- 章记忆 ``memory/<seq:04d>.md``：六节结构（事件/角色经历/信息项/知晓关系/承诺与伏笔/
  **状态更新**），由 ``write_chapter_memory`` 整文件重写；``read_chapter_memory`` 按同格式
  解析回 layers dict（解析失败返 None 不 raise）。
  **第 6 节「状态更新」是本仓自加的第 5 层**（P18，R1 复活）：seed 的记忆格式是四层，
  ``state.md`` 只存最新值 → 审计的 R1（数值单调）拿不到「相邻两次更新」→ 恒空。
  本节按章保留**本章模型明确给出**的角色状态字段（realm/level/location/affiliation/
  status/notes），逐字段一行 ``- 角色｜字段：值``；**旧文件（无本节）照常解析**
  （``state_updates`` 为空列表 → R1 仍不产出候选，行为与从前逐字相同）。
- 现状 ``memory/state.md``：三节骨架（# 现状快照 / ## 角色现状 / ## 世界现状，
  建项目时脚手架已创建）。现状是记忆日志的投影：单调向前、可手改——
  ``apply_state_updates`` 照 seed :193-260 语义：只写模型明确给出的字段（未提的
  保留原值，防手改被冲）、``seq < updated`` 整角色跳过（单调守卫）、``locked``
  列表内字段跳过、status 只接受 alive/dead/exited/missing（非法值跳过该字段，
  不 raise）、世界按 (kind,key) upsert、last_seen 只增。

``rename_chapter_seq`` 是 seed 仓储 ``update_*_chapter`` 的文件等价物：文件改名 +
文件内「第N章」锚点改写（信息项「源自第N章」、知晓「第N章起」、承诺「第N章埋/
到期第N章」——到期锚点跨文件全局重映射，同 seed 的 update_due_chapter）+
state.md 的 updated/last_seen 数值重映射。

锁/写盘模式复用 ``store/project.py`` 的同款 ``_hold``（逐文件锁、排序加防死锁）/
``_write``（mkdir + write_text）——复用模式不继承。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from harness.text import as_list, as_text

__all__ = ["MemoryStore"]

log = logging.getLogger("memory.store")

_MEMORY_NAME = re.compile(r"^(\d{4})\.md$")
_HEADER = re.compile(r"^# 第(\d+)章记忆《(.*?)》\s*$")
_EVENT_LINE = re.compile(r"^- \[全知\] (.*?) — (.*?)（(.*)）$")
_CEVENT_LINE = re.compile(r"^- (.*?)（逐字：「(.*?)」(.*)）$")
_INFO_LINE = re.compile(r"^- (.*?)：(.*?)（revealed_to：(.*?)；源自第(\d+)章(.*)）$")
_KNOW_LINE = re.compile(r"^- (.*?) 知道「(.*?)」（第(\d+)章起）$")
_PROMISE_LINE = re.compile(
    r"^- \[(?P<kind>[^\]]*)\] (?P<content>.*?)——(?P<by>.*?)"
    r"（第(?P<made>\d+)章埋；(?:到期第(?P<due>\d+)章；)?状态 (?P<status>[^；]*)；(?P<resolution>.*)）$")
# 第 5 层「状态更新」行：``- 角色｜字段：值``（角色名与字段名不得含 ｜／：）
_STATE_UPDATE_LINE = re.compile(
    r"^- (?P<character>[^｜]+)｜(?P<field>[^：:]+)[:：](?P<value>.*)$")
_WORLD_LINE = re.compile(
    r"^- \[(?P<kind>[^\]]*)\] (?P<key>.*?)｜(?P<label>.*?)：(?P<value>.*?)"
    r"（importance (?P<imp>\d+)；(?P<anno>.*)）$")
_CHAPTER_REF = re.compile(r"第(\d+)章")

# apply_state_updates 的 seed 字段与合法值
_STATE_FIELDS = ("realm", "level", "location", "affiliation", "notes")
_LEGAL_STATUS = ("alive", "dead", "exited", "missing")
_CHAR_PINNED = {"status", "realm", "level", "location", "affiliation", "notes",
                "updated", "last_seen", "locked"}
_CEVENT_EXTRA = ("acted", "felt", "got_lost", "secrets")
_KINDS = ("promise", "foreshadow", "cliffhanger")


def _as_int(v, default: int = 0) -> int:
    """尽力转整数（现状的 updated/last_seen 可能被手改成字符串）。seed copy。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _one_line(value) -> str:
    """把值压成**单行**文本（写盘用）。

    **为什么**（2026-11-17 独立复审 P18-1）：记忆文件是**行式**格式（一行一条），
    而模型可能给出**含换行**的值（`notes` 写成多行、或返回**数组**——``as_text``
    用 ``\\n`` 连接）→ 写进去会**截断**（行式解析只取第一行，后面的行成了野条目）
    → 历史被静默改写（第 5 节的 level 甚至可能被截成 "9"）。
    → **写盘前一律压成单行**（换行/回车 → 空格）。
    """
    text = as_text(value) or ""
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


class MemoryStore:
    """记忆文件读写入口。``root`` 为项目根目录（含 ``memory/``）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks: dict[Path, threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ 锁

    @contextmanager
    def _hold(self, *paths: Path):
        """全程持有这些文件路径的锁（排序后加锁，避免交叉死锁）。project.py 同款。"""
        keys = sorted({Path(p) for p in paths}, key=str)
        locks = []
        for key in keys:
            with self._guard:
                lock = self._locks.setdefault(key, threading.Lock())
            locks.append(lock)
        for lock in locks:
            lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # -------------------------------------------------------------- 章记忆

    def _chapter_path(self, seq: int) -> Path:
        return self.root / "memory" / f"{seq:04d}.md"

    def write_chapter_memory(self, seq: int, title: str, layers: dict) -> None:
        """整文件重写一章记忆（五层：events/character_events/info_updates/promise_updates/
        **state_updates**）。

        知晓关系节由 info_updates 的 revealed_to 投影；重复提取前先 ``purge_chapter``
        清旧文件，避免条目堆积（幂等由调用方保证，本方法只负责重写）。
        """
        layers = layers if isinstance(layers, dict) else {}
        path = self._chapter_path(seq)
        with self._hold(path):
            self._write(path, self._render_chapter_memory(seq, title, layers))

    @staticmethod
    def _render_chapter_memory(seq: int, title: str, layers: dict) -> str:
        lines = [f"# 第{seq}章记忆《{_one_line(title)}》", "", "## 事件"]
        for ev in as_list(layers.get("events")):
            if not isinstance(ev, dict):
                continue
            chars = [_one_line(c) for c in as_list(ev.get("characters"))]
            segs = [_one_line(ev.get("location")),
                    _one_line(ev.get("time_marker")),
                    "人物：" + "、".join(c for c in chars if c)]
            new_info = [_one_line(x) for x in as_list(ev.get("new_info"))]
            new_info = [x for x in new_info if x]
            if new_info:
                segs.append("new_info：" + "、".join(new_info))
            foreshadow = _one_line(ev.get("foreshadow")).strip()
            if foreshadow:
                segs.append("foreshadow：" + foreshadow)
            lines.append(f"- [全知] {_one_line(ev.get('title'))} — "
                         f"{_one_line(ev.get('summary'))}（{'；'.join(segs)}）")
        lines += ["", "## 角色经历"]
        for ce in as_list(layers.get("character_events")):
            if not isinstance(ce, dict):
                continue
            lines.append(f"### {(_one_line(ce.get('character'))).strip()}")
            anno = [f"逐字：「{_one_line(ce.get('quotes'))}」"]
            for key in _CEVENT_EXTRA:
                val = _one_line(ce.get(key)).strip()
                if val:
                    anno.append(f"{key}：{val}")
            lines.append(f"- {_one_line(ce.get('summary'))}（{'；'.join(anno)}）")
        lines += ["", "## 信息项"]
        revealed_pairs: list[tuple[str, list[str]]] = []
        for iu in as_list(layers.get("info_updates")):
            if not isinstance(iu, dict):
                continue
            item_title = _one_line(iu.get("title")).strip()
            if not item_title:
                continue
            revealed = [_one_line(x) for x in as_list(iu.get("revealed_to"))]
            revealed = [r for r in revealed if r]
            lines.append(f"- {item_title}：{_one_line(iu.get('content'))}"
                         f"（revealed_to：{'、'.join(revealed)}；源自第{seq}章）")
            revealed_pairs.append((item_title, revealed))
        lines += ["", "## 知晓关系"]
        for item_title, revealed in revealed_pairs:
            for name in revealed:
                lines.append(f"- {name} 知道「{item_title}」（第{seq}章起）")
        lines += ["", "## 承诺与伏笔"]
        for pu in as_list(layers.get("promise_updates")):
            if not isinstance(pu, dict):
                continue
            content = _one_line(pu.get("content")).strip()
            if not content:
                continue
            kind = pu.get("kind") if pu.get("kind") in _KINDS else "promise"
            by = _one_line(pu.get("by_character")).strip()
            status = _one_line(pu.get("status")).strip()
            resolution = _one_line(pu.get("resolution"))
            due = pu.get("due")
            due_seg = f"到期第{_as_int(due)}章；" if due not in (None, "") else ""
            lines.append(f"- [{kind}] {content}——{by}（第{seq}章埋；{due_seg}"
                         f"状态 {status}；{resolution}）")
        lines += ["", "## 状态更新"]
        for su in as_list(layers.get("state_updates")):
            if not isinstance(su, dict):
                continue
            character = _one_line(su.get("character")).strip()
            field = _one_line(su.get("field")).strip()
            value = _one_line(su.get("value"))
            if not (character and field) or not value:
                continue
            lines.append(f"- {character}｜{field}：{value}")
        return "\n".join(lines) + "\n"

    def read_chapter_memory(self, seq: int) -> dict | None:
        """按钉死格式解析回 dict；文件缺失/非 UTF-8/头不匹配返 None（不 raise）。

        单条解析失败跳过该条 + log.warning（同 store 层容错风格），不拖垮整章。
        """
        path = self._chapter_path(seq)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        lines = text.split("\n")
        head = _HEADER.match(lines[0].strip()) if lines else None
        if not head:
            return None
        out: dict = {"seq": int(head.group(1)), "title": head.group(2),
                     "events": [], "character_events": [], "info_updates": [],
                     "knowledge": [], "promise_updates": [], "state_updates": []}
        section: str | None = None
        cur_char: str | None = None
        for line in lines[1:]:
            s = line.strip()
            if s.startswith("## "):
                section = s[3:].strip()
                cur_char = None
                continue
            if s.startswith("### "):
                cur_char = s[4:].strip()
                continue
            if not s.startswith("- "):
                continue
            try:
                if section == "事件":
                    out["events"].append(_parse_event_line(s))
                elif section == "角色经历":
                    out["character_events"].append(_parse_cevent_line(s, cur_char or ""))
                elif section == "信息项":
                    out["info_updates"].append(_parse_info_line(s))
                elif section == "知晓关系":
                    out["knowledge"].append(_parse_knowledge_line(s))
                elif section == "承诺与伏笔":
                    out["promise_updates"].append(_parse_promise_line(s))
                elif section == "状态更新":
                    out["state_updates"].append(_parse_state_update_line(s))
            except (ValueError, IndexError) as exc:
                log.warning("read_chapter_memory 跳过坏条目 %s: %s: %s", path, s, exc)
        return out

    def purge_chapter(self, seq: int) -> None:
        """删 ``memory/<seq>.md``；幂等：不存在则 no-op。"""
        path = self._chapter_path(seq)
        with self._hold(path):
            if path.exists():
                path.unlink()

    def list_chapter_memories(self) -> list[int]:
        """已有记忆章号升序（``state.md`` 与 digests/ 天然被 ``\\d{4}.md`` 排除）。"""
        mem = self.root / "memory"
        if not mem.is_dir():
            return []
        seqs = []
        for path in mem.iterdir():
            m = _MEMORY_NAME.match(path.name)
            if m and path.is_file():
                seqs.append(int(m.group(1)))
        return sorted(seqs)

    # ----------------------------------------------------------------- 现状

    def read_state(self) -> dict:
        """``{"characters": {name: {...}}, "world": {(kind,key): {...}}}``。

        文件缺失/解析失败返空 dict（兼容脚手架三节空骨架），不 raise。
        """
        return self._read_state()

    def _read_state(self) -> dict:
        state = {"characters": {}, "world": {}}
        path = self.root / "memory" / "state.md"
        if not path.exists():
            return state
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return state
        section: str | None = None
        for line in text.split("\n"):
            s = line.strip()
            if s.startswith("## "):
                section = s[3:].strip()
                continue
            if not s.startswith("- "):
                continue
            if section == "角色现状":
                entry = _parse_character_line(s)
                if entry:
                    state["characters"][entry.pop("name")] = entry
            elif section == "世界现状":
                entry = _parse_world_line(s)
                if entry:
                    state["world"][(entry["kind"], entry["key"])] = entry
        return state

    def apply_state_updates(self, updates: dict, seq: int) -> None:
        """把 state_updates 投影进 state.md（seed :193-260 语义，见模块 docstring）。

        只写明确给出的字段；单调守卫 ``seq < updated`` 整角色跳过；``locked`` 字段
        跳过；status 只接受 alive/dead/exited/missing（非法值跳过该字段 + 记日志）；
        世界按 (kind,key) upsert；last_seen 只增。整文件重写但保留三节骨架结构。
        """
        updates = updates if isinstance(updates, dict) else {}
        path = self.root / "memory" / "state.md"
        with self._hold(path):
            state = self._read_state()
            chars = state["characters"]
            for su in (x for x in as_list(updates.get("characters")) if isinstance(x, dict)):
                name = (as_text(su.get("name")) or "").strip()
                if not name:
                    continue
                prev = chars.get(name) or {}
                if seq < _as_int(prev.get("updated")):
                    continue    # 单调守卫：旧章的现状不得覆盖新章的（seed :206）
                nxt = dict(prev)
                locked = prev.get("locked")
                locked_fields = {str(x) for x in locked} if isinstance(locked, list) else set()
                for key in _STATE_FIELDS:
                    if key in locked_fields:
                        continue    # 手改锁定：自动更新不得覆盖（seed :209-218）
                    val = su.get(key)
                    if val not in (None, ""):
                        nxt[key] = _one_line(val)
                st = (as_text(su.get("status")) or "").strip()
                if st and st not in _LEGAL_STATUS:
                    log.warning("apply_state_updates 非法 status %r（%s），字段已跳过",
                                st, name)
                if st in _LEGAL_STATUS and "status" not in locked_fields:
                    nxt["status"] = st
                nxt["updated"] = seq
                if _as_int(prev.get("last_seen")) < seq:
                    nxt["last_seen"] = seq    # last_seen 单调只增（seed :250-260）
                chars[name] = nxt
            world = state["world"]
            for wi in (x for x in as_list(updates.get("world")) if isinstance(x, dict)):
                key = (as_text(wi.get("key")) or as_text(wi.get("label")) or "").strip()
                if not key:
                    continue
                kind = (as_text(wi.get("kind")) or "other").strip() or "other"
                prev = world.get((kind, key)) or {}
                if seq < _as_int(prev.get("updated")):
                    continue        # 单调守卫同角色
                nxt = dict(prev)
                nxt["kind"] = kind
                nxt["key"] = key
                label = as_text(wi.get("label"))
                if label:
                    nxt["label"] = label
                if wi.get("value") not in (None, ""):
                    nxt["value"] = _one_line(wi.get("value"))
                if isinstance(wi.get("meta"), dict):
                    nxt["meta"] = wi["meta"]
                if wi.get("importance") not in (None, ""):
                    nxt["importance"] = _as_int(wi.get("importance"), 3) or 3
                nxt["updated"] = seq
                world[(kind, key)] = nxt
            self._write(path, self._render_state(state))

    @staticmethod
    def _render_state(state: dict) -> str:
        lines = ["# 现状快照", "", "## 角色现状", ""]
        for name, entry in state["characters"].items():
            lines.append(_render_character_line(name, entry))
        lines += ["", "## 世界现状", ""]
        for item in state["world"].values():
            lines.append(_render_world_line(item))
        return "\n".join(lines).rstrip("\n") + "\n"

    # ------------------------------------------------------------ 章号重编

    def rename_chapter_seq(self, old: int, new: int) -> None:
        """文件改名 + 「第N章」锚点改写 + state.md 数值重映射 old→new。

        到期锚点跨文件全局重映射（seed update_due_chapter 的文件等价物）；目标文件
        已存在时响亮 raise（调用方批处理重编应先用 out-of-range 中转号避开冲突）。
        """
        if old == new:
            return
        mem = self.root / "memory"
        old_path = mem / f"{old:04d}.md"
        new_path = mem / f"{new:04d}.md"
        state_path = mem / "state.md"
        with self._hold(*[mem / f"{s:04d}.md" for s in self.list_chapter_memories()],
                        state_path):
            if old_path.exists() and new_path.exists():
                raise FileExistsError(
                    f"rename_chapter_seq: memory/{new:04d}.md 已存在，拒绝覆盖")
            for seq in self.list_chapter_memories():
                path = mem / f"{seq:04d}.md"
                text = path.read_text(encoding="utf-8")
                remapped = _remap_chapter_anchors(text, old, new)
                if path == old_path:
                    remapped = remapped.replace(
                        f"# 第{old}章记忆", f"# 第{new}章记忆", 1)
                if remapped != text:
                    self._write(path, remapped)
            if state_path.exists():
                text = state_path.read_text(encoding="utf-8")
                remapped = _remap_state_seq(text, old, new)
                if remapped != text:
                    self._write(state_path, remapped)
            if old_path.exists():
                old_path.rename(new_path)


# ------------------------------------------------------------------ 行级解析

def _parse_event_line(line: str) -> dict:
    m = _EVENT_LINE.match(line)
    if not m:
        raise ValueError("事件行格式不符")
    title, summary, anno = m.groups()
    segs = anno.split("；")
    location = segs[0] if len(segs) > 0 else ""
    time_marker = segs[1] if len(segs) > 1 else ""
    rest = segs[2:]
    characters: list[str] = []
    if rest and rest[0].startswith("人物："):
        characters = [c for c in rest.pop(0)[len("人物："):].split("、") if c]
    ev: dict = {"title": title, "summary": summary, "location": location,
                "time_marker": time_marker, "characters": characters}
    for seg in rest:
        key, sep, value = seg.partition("：")
        if not sep:
            continue
        if key == "new_info":
            ev["new_info"] = [x for x in value.split("、") if x]
        elif key == "foreshadow":
            ev["foreshadow"] = value
    return ev


def _parse_cevent_line(line: str, character: str) -> dict:
    m = _CEVENT_LINE.match(line)
    if not m:
        raise ValueError("角色经历行格式不符")
    summary, quotes, rest = m.groups()
    ce: dict = {"character": character, "summary": summary, "quotes": quotes}
    for key in _CEVENT_EXTRA:
        ce[key] = ""
    if rest:
        for seg in rest.lstrip("；").split("；"):
            key, sep, value = seg.partition("：")
            if sep and key in ce:
                ce[key] = value
    return ce


def _parse_info_line(line: str) -> dict:
    m = _INFO_LINE.match(line)
    if not m:
        raise ValueError("信息项行格式不符")
    title, content, revealed = m.group(1), m.group(2), m.group(3)
    return {"title": title, "content": content,
            "revealed_to": [x for x in revealed.split("、") if x]}


def _parse_knowledge_line(line: str) -> dict:
    m = _KNOW_LINE.match(line)
    if not m:
        raise ValueError("知晓关系行格式不符")
    return {"character": m.group(1), "info_title": m.group(2),
            "chapter": int(m.group(3))}


def _parse_promise_line(line: str) -> dict:
    m = _PROMISE_LINE.match(line)
    if not m:
        raise ValueError("承诺行格式不符")
    return {"kind": m.group("kind"), "content": m.group("content"),
            "by_character": m.group("by"), "due": _int_or_none(m.group("due")),
            "status": m.group("status"), "resolution": m.group("resolution")}


def _parse_state_update_line(line: str) -> dict:
    """第 5 层「状态更新」行 → {character, field, value}。

    行格式 ``- 角色｜字段：值``：角色名取第一个 ｜ 之前、字段名取第一个 ：之前、
    值取其余全部（值里允许有 ：）。
    """
    m = _STATE_UPDATE_LINE.match(line)
    if not m:
        raise ValueError("状态更新行格式不符")
    return {"character": m.group("character").strip(),
            "field": m.group("field").strip(),
            "value": m.group("value")}


def _int_or_none(v) -> int | None:
    return int(v) if v is not None else None


def _parse_character_line(line: str) -> dict | None:
    parts = line[2:].split("｜")
    name = parts[0].strip()
    if not name:
        return None
    entry: dict = {"name": name, "updated": 0, "last_seen": 0}
    for seg in parts[1:]:
        key, sep, value = seg.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key in ("updated", "last_seen"):
            m = _CHAPTER_REF.search(value)
            entry[key] = int(m.group(1)) if m else 0
        elif key == "locked":
            # 手改可能在行中间插入 locked 段而尾部仍留有空的 locked 段：空值不得
            # 覆盖已解析到的锁定字段
            if value:
                entry["locked"] = [x.strip() for x in value.split(",") if x.strip()]
            else:
                entry.setdefault("locked", [])
        else:
            entry[key] = value
    return entry


def _parse_world_line(line: str) -> dict | None:
    m = _WORLD_LINE.match(line)
    if not m:
        return None
    updated, meta = 0, None
    for seg in m.group("anno").split("；"):
        if seg.startswith("updated"):
            ref = _CHAPTER_REF.search(seg)
            if ref:
                updated = int(ref.group(1))
        elif seg.startswith("meta："):
            try:
                meta = json.loads(seg[len("meta："):])
            except ValueError:
                log.warning("world 条目 meta 解析失败，已丢弃: %s", seg)
    entry: dict = {"kind": m.group("kind").strip() or "other",
                   "key": m.group("key").strip(),
                   "label": m.group("label"), "value": m.group("value"),
                   "importance": _as_int(m.group("imp"), 3) or 3,
                   "updated": updated}
    if isinstance(meta, dict):
        entry["meta"] = meta
    return entry


# ------------------------------------------------------------------ 行级渲染

def _render_character_line(name: str, entry: dict) -> str:
    parts = [name]
    for key in ("status", "realm", "level", "location", "affiliation", "notes"):
        parts.append(f"{key}: {as_text(entry.get(key)) or ''}")
    updated = _as_int(entry.get("updated"))
    parts.append(f"updated: 第{updated}章" if updated else "updated: ")
    last_seen = _as_int(entry.get("last_seen"))
    parts.append(f"last_seen: 第{last_seen}章" if last_seen else "last_seen: ")
    locked = entry.get("locked")
    locked_s = ",".join(str(x) for x in locked) if isinstance(locked, list) \
        else (as_text(locked) or "")
    parts.append(f"locked: {locked_s}")
    for key, value in entry.items():    # 钉死字段外的手加键原样保留（防手改被冲）
        if key not in _CHAR_PINNED:
            parts.append(f"{key}: {as_text(value) or ''}")
    return "- " + "｜".join(parts)


def _render_world_line(item: dict) -> str:
    kind = (as_text(item.get("kind")) or "other").strip() or "other"
    label = as_text(item.get("label")) or ""
    value = as_text(item.get("value")) or ""
    importance = _as_int(item.get("importance"), 3) or 3
    updated = _as_int(item.get("updated"))
    anno = f"importance {importance}；updated 第{updated}章" if updated \
        else f"importance {importance}；updated "
    meta = item.get("meta")
    if isinstance(meta, dict) and meta:
        anno += f"；meta：{json.dumps(meta, ensure_ascii=False)}"
    return f"- [{kind}] {as_text(item.get('key')) or ''}｜{label}：{value}（{anno}）"


# ------------------------------------------------------------------ 章号重编

def _remap_chapter_anchors(text: str, old: int, new: int) -> str:
    """章记忆文件内的「第N章」锚点改写（标题行由调用方单独处理）。"""
    return (text.replace(f"源自第{old}章", f"源自第{new}章")
                .replace(f"第{old}章起", f"第{new}章起")
                .replace(f"第{old}章埋", f"第{new}章埋")
                .replace(f"到期第{old}章", f"到期第{new}章"))


def _remap_state_seq(text: str, old: int, new: int) -> str:
    """state.md 数值重映射：角色的 updated/last_seen 与世界的 updated。"""
    return (text.replace(f"updated: 第{old}章", f"updated: 第{new}章")
                .replace(f"last_seen: 第{old}章", f"last_seen: 第{new}章")
                .replace(f"updated 第{old}章", f"updated 第{new}章"))
