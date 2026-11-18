# harness.web.readers：七视图只读 API（Task 6）
"""前端七视图的 JSON 组装层（outline / chapters / chapter_detail / characters /
memory / chapter_memory / issues）。

铁律：

- **只组装不判定**：谁是主要角色、哪章被拦、谁无卡，一律从 ``harness.characters``
  取（spec X3 判定唯一源）；本模块**不得**重写任何判定逻辑。
- **blocked_by 恒为 list**（空 = ``[]``，绝不 null——Q12 钉死）。
- **flavor_score / review 快照**：读 ``.harness/review/<seq>.json``，缺档 / 坏 JSON /
  非 dict → ``None``，绝不让视图 500。
- **容错口径同 Task 4**：单项目坏文件降级（跳过 / None / 空表），不让视图 500；
  章 / 章记忆不存在 → ``FileNotFoundError``（**路由层**转 404）。
- 路由挂在 ``/api/project/{id}`` 下，见 ``harness.web.app``（Task 6 路由段）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from harness.characters import CastMember, blocked_by_chapter, carded_names, collect_candidates
from harness.memory.store import MemoryStore
from harness.run.issues import IssuesStore
from harness.store.frontmatter import FileFormatError
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import strip_edges

__all__ = [
    "chapter_detail",
    "chapter_memory_view",
    "chapters_view",
    "characters_view",
    "issues_view",
    "memory_view",
    "outline_view",
    "save_chapter_body",
]

log = logging.getLogger("harness.web.readers")

# memory_view 的「现状」投影：read_state 的 world 以 (kind, key) 元组为键，
# fastapi 的 jsonable_encoder 对元组键先编成 list 再当 dict 键 → unhashable TypeError，
# 故统一投影成 "kind|key" 字符串键；characters 与全部值保持原文。
WORLD_KEY_SEP = "|"

# 章记忆六节（显示名, layers 键）；empty_dimensions 按此顺序产出——
# 值为空 list 的维度名，前端据此显示「空：因为没有角色卡…」诚实文案。
_MEMORY_DIMENSIONS = (
    ("事件", "events"),
    ("角色经历", "character_events"),
    ("信息项", "info_updates"),
    ("知晓关系", "knowledge"),
    ("承诺与伏笔", "promise_updates"),
    ("状态更新", "state_updates"),
)


def _warn(path: Path, exc: FileFormatError) -> None:
    log.warning("视图 reader 跳过坏文件: %s（%s）", path, exc)


# ---------------------------------------------------------------- outline

def outline_view(store: ProjectStore) -> dict:
    """卷骨架 + 按卷分组的情节；卷号 = arcs 里的 int vol ∪ plots 目录里的 vol-N.md。"""
    arcs = store.read_arcs(on_error=_warn)
    vols: set[int] = set()
    for arc in arcs:
        vol = arc.get("vol") if isinstance(arc, dict) else None
        if isinstance(vol, int) and not isinstance(vol, bool):
            vols.add(vol)
    plots_dir = store.root / "outline" / "plots"
    if plots_dir.is_dir():
        for path in plots_dir.glob("vol-*.md"):
            try:
                vols.add(int(path.stem[len("vol-"):]))
            except ValueError:
                continue
    return {
        "arcs": arcs,
        "planned_volume_count": store.read_planned_volume_count(),
        "volumes": [{"vol": vol, "plots": store.read_plots(vol)} for vol in sorted(vols)],
    }


# ---------------------------------------------------------------- chapters

def chapters_view(store: ProjectStore) -> dict:
    """逐章列表；blocked_by 判定走 X3 唯一源，恒为 list（Q12：空 = []，绝不 null）。"""
    try:
        blocked = blocked_by_chapter(store)
    except FileFormatError as exc:
        # collect_candidates 内部的 list_chapters 不容错：坏章降级为空拦表，
        # 逐章列表照常返回（坏章本身经 on_error 跳过），不拖垮视图。
        log.warning("chapters 视图 blocked_by 降级为空（%s）", exc)
        blocked = {}
    chapters = []
    for doc in store.list_chapters(on_error=_warn):
        meta = doc.meta
        chapters.append({
            "seq": doc.seq,
            "title": meta.get("title"),
            "status": meta.get("status"),
            "plot_id": meta.get("plot_id"),
            "word_count": meta.get("word_count"),
            "flavor_score": _flavor_score(store.root, doc.seq),
            "blocked_by": blocked.get(doc.seq) or [],
        })
    return {"chapters": chapters}


def chapter_detail(store: ProjectStore, seq: int) -> dict:
    """单章 meta + 正文 + 审核快照；章不存在 → FileNotFoundError（路由层转 404）。"""
    doc = store.read_chapter(seq)  # 缺章 FileNotFoundError 原样上抛
    return {"seq": doc.seq, "meta": doc.meta, "body": doc.body,
            "path": str(doc.path) if doc.path is not None else "",
            "review": _review_snapshot(store.root, seq)}


def save_chapter_body(store: ProjectStore, seq: int, body: str) -> ChapterDoc:
    """PUT 章正文：定位 meta seq 命中的章 → 只换正文段写回其 ``doc.path`` → 返回新 doc。

    doc.path 纪律（§4.8）：章号取自 front-matter、与文件名前缀可能分叉（手写章常见），
    定位与写盘全程用 read 回来的 doc（内含 path），**绝不按 seq 重新 glob / 重推导
    文件名**——否则 PUT seq=5 会落到 ``0005-*.md``、改错章（review focus #1：文件
    ``0003-x.md`` 但 meta seq=5 时，必须写进 ``0003-x.md``，seq=3 的那个文件一个字节
    不动）。也不能走 ``store.write_chapter``：它按 ``doc.seq``+title 重推导文件名、
    删同前缀旧文件、重新序列化 front-matter，对手写章是破坏性的。

    不碰 front-matter：front-matter 原文逐字节保留，只替换正文段（与 audit/fix.py
    ``_rewrite_in_place`` 同一口径）；正文写盘过 ``strip_edges``（与 store 写盘口径一致）。
    章不存在 → ``FileNotFoundError``（路由层转 404）；任一章 front-matter 坏 →
    ``FileFormatError`` 原样上抛（路由层转 409，失败关闭：坏章绝不跳过猜着改）。
    """
    doc = next((d for d in store.list_chapters() if d.seq == seq), None)
    if doc is None or doc.path is None:
        raise FileNotFoundError(f"seq={seq} 的章节不存在")
    path = doc.path
    new_body = strip_edges(body)
    with store._hold(path):  # 与 store 写盘同口径：全程持该文件的锁（digest.py 同款跨模块复用）
        raw = path.read_text(encoding="utf-8")
        head = _frontmatter_head(raw)
        text = f"{head}\n\n{new_body}" if head else new_body  # 无 front-matter：整文件即正文
        path.write_text(text, encoding="utf-8", newline="\n")
    return ChapterDoc(seq=doc.seq, meta=doc.meta, body=new_body, path=path)


def _frontmatter_head(raw: str) -> str:
    """front-matter 原文（起始 ``---`` 到结束 ``---`` 为止，逐字节保留）。

    无 front-matter（首行非 ``---``）→ 空串：整文件都是正文，写回时只有正文段。
    """
    lines = raw.split("\n")
    if lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[:i + 1])
    return ""  # 有起始 --- 却无结束 ---：split_file 早已按 FileFormatError 挡掉，到不了这里


def _review_snapshot(root: Path, seq: int) -> dict | None:
    """``.harness/review/<seq>.json`` 全文；缺档 / 坏 JSON / 非 dict → None。"""
    data = _load_review(root, seq)
    return data if isinstance(data, dict) else None


def _flavor_score(root: Path, seq: int):
    """审核快照里的去味分；缺档 / 坏 JSON / 无该键 → None（视图不 500）。"""
    data = _load_review(root, seq)
    if not isinstance(data, dict):
        return None
    return data.get("flavor_score")


def _load_review(root: Path, seq: int):
    path = root / ".harness" / "review" / f"{seq}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("review 快照不可读（按 None 处理）: %s（%s）", path, exc)
        return None


# ---------------------------------------------------------------- characters

def characters_view(store: ProjectStore) -> dict:
    """主角 / 候选（各带 card dict|None）/ 龙套（裸 member）+ counts。

    三档划分与无卡判定全在 ``harness.characters``（X3 唯一源），这里只做 JSON 组装。
    counts：carded = 主要角色里有卡数；pending = 无卡数；walk_on = 龙套数
    （与 sidebar ``characters_pending``= uncarded_major 同口径）。
    """
    try:
        analysis = collect_candidates(store)
    except FileFormatError as exc:
        log.warning("characters 视图聚合失败（三档按空处理）: %s", exc)
        analysis = None
    carded = carded_names(store)
    cards = _cards_by_name(store)

    def _with_card(member: CastMember) -> dict:
        item = asdict(member)
        item["card"] = cards.get(member.name)  # 无卡（卡名与 alias 都不命中）→ None
        return item

    if analysis is None:
        protagonists: list[dict] = []
        candidates: list[dict] = []
        walk_ons: list[dict] = []
    else:
        protagonists = [_with_card(m) for m in analysis.protagonists]
        candidates = [_with_card(m) for m in analysis.candidates]
        walk_ons = [asdict(m) for m in analysis.walk_ons]
    majors = protagonists + candidates
    n_carded = sum(1 for m in majors if m["name"] in carded)
    return {
        "protagonists": protagonists,
        "candidates": candidates,
        "walk_ons": walk_ons,
        "counts": {"carded": n_carded, "pending": len(majors) - n_carded,
                   "walk_on": len(walk_ons)},
    }


def _cards_by_name(store: ProjectStore) -> dict[str, dict]:
    """卡名与 alias 都映射到整张卡 dict（list_characters 的 meta）。"""
    out: dict[str, dict] = {}
    for card in store.list_characters():
        keys = [card.get("name"), *(card.get("aliases") or [])]
        for key in keys:
            name = str(key).strip()
            if name:
                out[name] = card
    return out


# ---------------------------------------------------------------- memory

def memory_view(store: ProjectStore) -> dict:
    """现状（read_state 投影）+ state.md 原文 + 卷级 digest 列表 + 章记忆 seq 列表。"""
    mem = MemoryStore(store.root)
    state_path = store.root / "memory" / "state.md"
    try:
        state_text = state_path.read_text(encoding="utf-8") if state_path.exists() else None
    except UnicodeDecodeError:
        log.warning("state.md 坏编码（state_text 按 None）: %s", state_path)
        state_text = None
    return {
        "state": _jsonable_state(mem.read_state()),
        "state_text": state_text,
        "digests": _digests(store.root),
        "chapter_memories": mem.list_chapter_memories(),
    }


def _jsonable_state(state: dict) -> dict:
    """read_state 原文投影：world 的 (kind, key) 元组键 → "kind|key" 字符串键。"""
    world = state.get("world")
    if not isinstance(world, dict):
        world = {}
    return {"characters": state.get("characters") or {},
            "world": {f"{kind}{WORLD_KEY_SEP}{key}": value
                      for (kind, key), value in world.items()}}


def _digests(root: Path) -> list[dict]:
    digests_dir = root / "memory" / "digests"
    out = []
    if digests_dir.is_dir():
        for path in digests_dir.glob("vol-*.md"):
            try:
                vol = int(path.stem[len("vol-"):])
            except ValueError:
                continue
            out.append({"vol": vol, "path": str(path)})
    return sorted(out, key=lambda d: d["vol"])


def chapter_memory_view(store: ProjectStore, seq: int) -> dict:
    """单章记忆原文 + empty_dimensions（值为空 list 的维度显示名）。

    章记忆缺失 / 不可解析 → FileNotFoundError（路由层转 404）。
    """
    data = MemoryStore(store.root).read_chapter_memory(seq)
    if data is None:
        raise FileNotFoundError(f"memory/{seq:04d}.md 不存在或不可解析")
    empty = [label for label, key in _MEMORY_DIMENSIONS if not data.get(key)]
    return {**data, "empty_dimensions": empty}


# ---------------------------------------------------------------- issues

def issues_view(store: ProjectStore, *, source: str | None,
                status: str | None) -> list[dict]:
    """台账逐字段转 dict（含 quote/chapter）；source / status 均为可选过滤。

    ``status=None`` 不过滤（IssuesStore.list(None) = 全部）；``source`` 在 store
    过滤后二次按字段过滤。
    """
    issues = IssuesStore(store.root).list(status)
    if source is not None:
        issues = [i for i in issues if i.source == source]
    return [asdict(issue) for issue in issues]
