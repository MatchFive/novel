# ProjectStore：文件即数据库（设计文档 §store / 附录 C.6、E.4）
"""小说项目的统一读写入口：章 / 世界观 / 角色 / 卷骨架 / 情节。

后续所有阶段（P1+）只通过本类碰项目文件，不直接读写磁盘。写入纪律：

- 正文类内容写盘前过 `strip_edges`（`render_document` 内建）；
- **每个文件路径一把 `threading.Lock`**（`self._locks`）：同文件串行、异文件可并行，
  每个 write 方法全程持对应文件的锁；
- front-matter 解析失败 → 备份 `*.bak` + 抛 `FileFormatError`，绝不静默覆盖
  （`split_file` 内建）；
- 聚合文件（world.md / vol-N.md）写前重读合并：解析时记录每个条目的原始文本区间，
  重写时只替换被 upsert 的条目块，未触碰条目（含用户手改内容）逐字保留。
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from harness.projects import slugify
from harness.store.frontmatter import FileFormatError, render_document, split_file
from harness.text import as_text, strip_edges

__all__ = ["ChapterDoc", "ProjectStore"]

log = logging.getLogger("store.project")

_CHAPTER_NAME = re.compile(r"^(\d{4})-.*\.md$")
_IMPORTANCE = re.compile(r"^-\s*重要度\s*[：:]\s*(.*)$")
_STATUS = re.compile(r"^-\s*状态\s*[：:]\s*(.*)$")
_KV = re.compile(r"^([^\s：:][^：:]{0,40}?)[：:]\s*(.*)$")
_PLOT_HEAD = re.compile(r"^###\s+(\d+)\s*[.、]\s*(.*)$")
_VOL_NAME = re.compile(r"^vol-(\d+)\.md$")


@dataclass
class ChapterDoc:
    """一章：front-matter meta（必含 seq/title/status）+ 正文。

    ``path`` 是该章**自己的文件路径**（从 store 读出来的章才有；手工构造的章为 None）。
    章号取自 frontmatter，与**文件名前缀可能不一致**（手写章常见）——要就地改写某章
    时必须用这个路径，不能按 seq 再 glob 一次（会落到别的章）。
    """

    seq: int
    meta: dict
    body: str
    path: Path | None = None


class ProjectStore:
    """项目文件读写入口。``root`` 为项目根目录（``ProjectInfo.path``）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks: dict[Path, threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ 锁

    @contextmanager
    def _hold(self, *paths: Path):
        """全程持有这些文件路径的锁（排序后加锁，避免交叉死锁）。"""
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

    # ------------------------------------------------------------------ 章

    def _chapter_file(self, seq: int, title: str) -> Path:
        return self.root / "chapters" / f"{seq:04d}-{slugify(title)}.md"

    def _chapter_files(self, seq: int) -> list[Path]:
        chapters = self.root / "chapters"
        if not chapters.is_dir():
            return []
        prefix = f"{seq:04d}-"
        return sorted(p for p in chapters.iterdir()
                      if p.name.startswith(prefix) and p.name.endswith(".md"))

    def chapter_file_path(self, seq: int) -> Path | None:
        """某章的**原始文件路径**（首个 ``{seq:04d}-*.md``；无则 None）。

        就地改写正文用：只换文件里的那一段，其余逐字节保留（文件名与 front-matter
        原文都不动）——``write_chapter`` 会按 title 重命名文件、删原文件、重新序列化
        front-matter，对手写章是破坏性的。
        """
        files = self._chapter_files(seq)
        return files[0] if files else None

    def read_chapter(self, seq: int) -> ChapterDoc:
        """读某章（按**文件名前缀** ``{seq:04d}-*.md`` 找文件）。

        ``path`` 填**该章自己的文件路径**（与 ``list_chapters`` 一致）——拿这个结果去
        就地改写正文时不得再按 seq glob 一次：章号取自 front-matter，与文件名前缀可能
        不一致（手写章常见），再 glob 一次会落到别的章上。
        """
        files = self._chapter_files(seq)
        if not files:
            raise FileNotFoundError(f"chapters/{seq:04d}-*.md 不存在")
        meta, body = split_file(files[0])
        return ChapterDoc(seq=int(meta.get("seq", seq)), meta=meta, body=body, path=files[0])

    def write_chapter(self, doc: ChapterDoc) -> Path:
        title = str(doc.meta.get("title") or doc.seq)
        target = self._chapter_file(doc.seq, title)
        # 标题变了会改名：同 seq 的旧文件一并加锁，写完后删掉，绝不留两份
        others = [p for p in self._chapter_files(doc.seq) if p != target]
        with self._hold(target, *others):
            self._write(target, render_document(doc.meta, doc.body))
            for old in others:
                old.unlink()
        return target

    def list_chapters(self, on_error: Callable[[Path, FileFormatError], None] | None = None) -> list[ChapterDoc]:
        """列出全部章节（按 seq 排序）。

        ``on_error`` 给了就逐文件容错：单个坏章（FileFormatError）回调后跳过，
        不拖垮整个列表；没给则照旧上抛。
        """
        chapters = self.root / "chapters"
        if not chapters.is_dir():
            return []
        docs = []
        for path in sorted(chapters.iterdir()):
            m = _CHAPTER_NAME.match(path.name)
            if m and path.is_file():
                try:
                    meta, body = split_file(path)
                except FileFormatError as exc:
                    if on_error is None:
                        raise
                    on_error(path, exc)
                    continue
                docs.append(ChapterDoc(seq=int(meta.get("seq", int(m.group(1)))),
                                       meta=meta, body=body, path=path))
        return sorted(docs, key=lambda d: d.seq)

    # ---------------------------------------------------------------- 世界观

    @staticmethod
    def _render_world_entry(entry: dict) -> str:
        importance = str(entry.get("importance") or "中")
        summary = strip_edges(as_text(entry.get("summary")) or "")
        content = strip_edges(as_text(entry.get("content")) or "")
        return "\n".join([
            f"### {entry['title']}",
            f"- 重要度：{importance}",
            "",
            summary,
            "",
            content,
        ])

    @staticmethod
    def _parse_world(text: str) -> tuple[str, list[tuple]]:
        """把 world.md 解析成 (前缀, 块列表)。

        块 = ("cat", 分类名, raw_lines) 或 ("entry", 所属分类, 条目标题, raw_lines)。
        每个块的 raw 覆盖到下一个块标题行（不含）为止，未触碰块的 raw 重写时逐字保留。
        """
        lines = text.split("\n")
        blocks: list[tuple] = []
        prefix: list[str] = []
        category = ""
        i = 0
        n = len(lines)
        # 前缀：第一个 ## 之前的全部内容（# 世界观 标题行等）
        while i < n and not lines[i].startswith("## "):
            prefix.append(lines[i])
            i += 1
        while i < n:
            line = lines[i]
            if line.startswith("## "):
                category = line[3:].strip()
                start = i
                i += 1
                while i < n and not lines[i].startswith(("# ", "## ", "### ")):
                    i += 1
                blocks.append(("cat", category, lines[start:i]))
            elif line.startswith("### "):
                title = line[4:].strip()
                start = i
                i += 1
                while i < n and not lines[i].startswith(("# ", "## ", "### ")):
                    i += 1
                blocks.append(("entry", category, title, lines[start:i]))
            else:  # 游离行（不应出现）：并入前缀，重写时保留
                prefix.append(line)
                i += 1
        return "\n".join(prefix).rstrip("\n"), blocks

    @staticmethod
    def _render_world(prefix: str, blocks: list[tuple]) -> str:
        parts = [prefix] if prefix else []
        for block in blocks:
            parts.append("\n".join(block[-1]).rstrip("\n"))
        return "\n\n".join(p for p in parts if p) + "\n"

    def read_world(self) -> list[dict]:
        world = self.root / "world.md"
        if not world.exists():
            return []
        _, blocks = self._parse_world(world.read_text(encoding="utf-8"))
        entries = []
        for block in blocks:
            if block[0] != "entry":
                continue
            _, category, title, raw_lines = block
            entries.append(self._parse_world_entry(category, title, raw_lines))
        return entries

    @staticmethod
    def _parse_world_entry(category: str, title: str, raw_lines: list[str]) -> dict:
        importance = "中"
        body_lines = raw_lines[1:]
        if body_lines:
            m = _IMPORTANCE.match(body_lines[0].strip())
            if m:
                importance = m.group(1).strip() or "中"
                body_lines = body_lines[1:]
        # 首段 = 摘要，其余 = 正文
        paragraphs: list[list[str]] = [[]]
        for line in body_lines:
            if line.strip() == "":
                if paragraphs[-1]:
                    paragraphs.append([])
            else:
                paragraphs[-1].append(line)
        paragraphs = [p for p in paragraphs if p]
        summary = "\n".join(paragraphs[0]).strip() if paragraphs else ""
        content = "\n\n".join("\n".join(p) for p in paragraphs[1:])
        return {"title": title, "category": category, "summary": summary,
                "content": content, "importance": importance}

    def upsert_world_entries(self, entries: list[dict]) -> int:
        """按 title 合并进 world.md；未触碰条目逐字保留。返回写入条数。"""
        world = self.root / "world.md"
        with self._hold(world):
            if world.exists():
                prefix, blocks = self._parse_world(world.read_text(encoding="utf-8"))
            else:
                prefix, blocks = "# 世界观", []
            index = {b[2]: i for i, b in enumerate(blocks) if b[0] == "entry"}
            for entry in entries:
                title = str(entry["title"])
                rendered = self._render_world_entry(entry).split("\n")
                if title in index:  # 已有：整块替换
                    old = blocks[index[title]]
                    blocks[index[title]] = ("entry", old[1], title, rendered)
                    continue
                category = str(entry.get("category") or "未分类")
                # 新条目：追加到对应分类小节的末尾；分类不存在则新建分类
                cat_idx = next((i for i, b in enumerate(blocks)
                                if b[0] == "cat" and b[1] == category), None)
                new_block = ("entry", category, title, rendered)
                if cat_idx is None:
                    blocks.append(("cat", category, [f"## {category}"]))
                    blocks.append(new_block)
                    index[title] = len(blocks) - 1
                    continue
                insert_at = cat_idx + 1
                for i in range(cat_idx + 1, len(blocks)):
                    if blocks[i][0] == "cat":
                        break
                    insert_at = i + 1
                blocks.insert(insert_at, new_block)
                index = {b[2]: i for i, b in enumerate(blocks) if b[0] == "entry"}
            self._write(world, self._render_world(prefix, blocks))
        return len(entries)

    # ---------------------------------------------------------------- 角色

    def _character_file(self, name: str) -> Path:
        return self.root / "characters" / f"{name}.md"

    def read_character(self, name: str) -> dict:
        """返回角色卡 front-matter dict（P0 最小语义；P4 角色卡深化时再扩展）。"""
        meta, _ = split_file(self._character_file(name))
        return meta

    def upsert_character(self, card: dict) -> Path:
        name = str(card["name"])
        path = self._character_file(name)
        with self._hold(path):
            # P0 不做六键结构：正文为占位标题 + 键值小节，meta 原样往返
            body_lines = [f"# {name}", ""]
            for key, value in card.items():
                if key == "name" or value is None:
                    continue
                body_lines.append(f"{key}：{as_text(value)}")
            self._write(path, render_document(dict(card), "\n".join(body_lines)))
        return path

    def rename_character(self, old: str, new: str) -> Path:
        old_path = self._character_file(old)
        new_path = self._character_file(new)
        with self._hold(old_path, new_path):
            meta, body = split_file(old_path)
            meta["name"] = new
            self._write(new_path, render_document(meta, body))
            old_path.unlink()
        return new_path

    def delete_character(self, name: str) -> None:
        """删角色卡文件；缺卡抛 FileNotFoundError（同 :meth:`read_character` 口径）。"""
        path = self._character_file(name)
        with self._hold(path):
            path.unlink()

    def list_characters(self) -> list[dict]:
        """列出全部角色卡（文件名排序）：{name, role_type, aliases}。

        单卡 front-matter 解析失败 → 备份 + 警告 + 跳过（坏卡经自己的渠道暴露，
        不拖垮整个名册）。
        """
        characters = self.root / "characters"
        if not characters.is_dir():
            return []
        cards = []
        for path in sorted(characters.iterdir()):
            if not (path.is_file() and path.name.endswith(".md")):
                continue
            try:
                meta, _ = split_file(path)
            except FileFormatError as exc:
                log.warning("list_characters 跳过坏卡: %s", exc)
                continue
            aliases = meta.get("aliases")
            if isinstance(aliases, str):  # 手改标量不当字符序列拆
                aliases = [aliases]
            elif not isinstance(aliases, list):
                aliases = []
            cards.append({"name": str(meta.get("name") or path.stem),
                          "role_type": str(meta.get("role_type") or ""),
                          "aliases": aliases})
        return cards

    # ------------------------------------------------------------ 卷骨架+情节

    def _read_arcs_meta(self) -> dict:
        """arcs.md 的**全部** front-matter（``arcs`` 列表 + 同级键如 planned_volume_count）。

        解析失败 → 空 dict（**不抛**）：坏 arcs.md 不该让只读路径炸（与 read_arcs 同款降级）。
        """
        path = self.root / "outline" / "arcs.md"
        if not path.exists():
            return {}
        try:
            meta, _ = split_file(path)
        except FileFormatError:
            log.warning("arcs.md 解析失败（按空处理）: %s", path)
            return {}
        return meta if isinstance(meta, dict) else {}

    def read_planned_volume_count(self) -> int | None:
        """模型给的预计总卷数（没有 → None）。"""
        value = self._read_arcs_meta().get("planned_volume_count")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    def read_arcs(self, on_error: Callable[[Path, FileFormatError], None] | None = None
                  ) -> list[dict]:
        """读卷骨架（``outline/arcs.md`` 的 front-matter）。

        ``on_error`` 给了就**逐文件容错**：坏 arcs.md（front-matter 坏 YAML）回调后按
        **零卷**处理，不拖垮整条链；没给则照旧上抛（调用方自己负责分类）。
        （2026-11-15 独立审核 M7：坏 arcs.md 曾让规划链三个工具抛 ``FileFormatError``
        → 穿出工具 → 违反「失败不 raise」。口径同 ``list_chapters``。）
        """
        arcs = self.root / "outline" / "arcs.md"
        if not arcs.exists():
            return []
        try:
            meta, _ = split_file(arcs)
        except FileFormatError as exc:
            if on_error is None:
                raise
            on_error(arcs, exc)
            return []
        return list(meta.get("arcs") or [])

    def upsert_arcs(self, arcs: list[dict], planned_volume_count: int | None = None) -> None:
        """按 vol 合并进 arcs.md 的 front-matter（写前重读合并）。

        ``planned_volume_count``：模型给的**预计总卷数**（P13）→ 与 ``arcs`` 同级存进
        front-matter，供段二注入「全书参考章数」用（此前用**实际弧数**顶替 →
        「预计 20 卷、只规划了 3 卷」时参考章数被算成 1/3）。

        合并索引只认 int vol（bool 排除——True==1 会顶掉真第 1 卷；list/dict vol 不可哈希）。
        非 int vol 的存量/新进条目都原样保留（追加），绝不互相覆盖、不炸 TypeError。
        """
        path = self.root / "outline" / "arcs.md"
        with self._hold(path):
            merged = self.read_arcs()
            index = {a.get("vol"): i for i, a in enumerate(merged)
                     if isinstance(a, dict)
                     and isinstance(a.get("vol"), int) and not isinstance(a.get("vol"), bool)}
            for arc in arcs:
                if not isinstance(arc, dict):  # 坏条目跳过 + 警告（本文件的容错风格），不互相覆盖
                    log.warning("upsert_arcs 跳过非 dict 条目: %r", arc)
                    continue
                vol = arc.get("vol")
                if isinstance(vol, int) and not isinstance(vol, bool):
                    if vol in index:
                        merged[index[vol]] = arc
                        continue
                    index[vol] = len(merged)
                merged.append(arc)
            doc: dict = {"arcs": merged}
            if planned_volume_count is not None:
                doc["planned_volume_count"] = planned_volume_count
            else:
                # 存量保留：一次不带该参数的写**不该**抹掉已有的预计卷数
                prev = self._read_arcs_meta().get("planned_volume_count")
                if prev is not None:
                    doc["planned_volume_count"] = prev
            self._write(path, render_document(doc, ""))

    def _vol_file(self, vol: int) -> Path:
        return self.root / "outline" / "plots" / f"vol-{vol:02d}.md"

    @staticmethod
    def _render_plot(plot: dict) -> str:
        lines = [f"### {plot['seq']}. {plot['title']}",
                 f"- 状态: {plot.get('status') or 'planned'}", ""]
        rest = {k: v for k, v in plot.items() if k not in ("seq", "title", "status") and v is not None}
        if "summary" in rest:  # 概要固定排第一
            lines.append(f"概要：{as_text(rest.pop('summary'))}")
        for key, value in rest.items():
            text = as_text(value)
            if "\n" in (text or ""):
                first, *others = (text or "").split("\n")
                lines.append(f"{key}：{first}")
                lines.extend(f"  {line}" for line in others)
            else:
                lines.append(f"{key}：{text}")
        return "\n".join(lines)

    @staticmethod
    def _parse_plots(text: str) -> tuple[str, list[tuple]]:
        """vol-N.md → (前缀, [(seq, title, raw_lines), ...])，同 _parse_world 的区间保留。"""
        lines = text.split("\n")
        prefix: list[str] = []
        blocks: list[tuple] = []
        i = 0
        n = len(lines)
        while i < n:
            m = _PLOT_HEAD.match(lines[i])
            if not m:
                prefix.append(lines[i])
                i += 1
                continue
            start = i
            i += 1
            while i < n and not _PLOT_HEAD.match(lines[i]):
                i += 1
            blocks.append((int(m.group(1)), m.group(2).strip(), lines[start:i]))
        return "\n".join(prefix).rstrip("\n"), blocks

    @staticmethod
    def _parse_plot_block(seq: int, title: str, raw_lines: list[str]) -> dict:
        status = "planned"
        extra: dict[str, str] = {}
        free: list[str] = []
        last_key: str | None = None  # 最近一条 KV 的映射键，供多行值续行回挂
        for line in raw_lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            m = _STATUS.match(stripped)
            if m:
                status = m.group(1).strip() or "planned"
                last_key = None
                continue
            if line.startswith("  "):  # 多行值的续行
                if last_key is not None:
                    extra[last_key] += "\n" + stripped
                elif free:
                    free[-1] += "\n" + stripped
                continue
            kv = _KV.match(stripped)
            if kv:
                key, value = kv.group(1).strip(), kv.group(2).strip()
                last_key = "summary" if key == "概要" else key
                extra.setdefault(last_key, value)
            else:
                free.append(stripped)
                last_key = None
        if free:
            extra.setdefault("content", "\n".join(free))
        return {"seq": seq, "title": title, "status": status, **extra}

    def read_plots(self, vol: int | None = None) -> list[dict]:
        if vol is not None:
            files = [self._vol_file(vol)]
        else:
            plots_dir = self.root / "outline" / "plots"
            files = []
            if plots_dir.is_dir():
                vols = []
                for p in plots_dir.iterdir():
                    m = _VOL_NAME.match(p.name)
                    if m and p.is_file():
                        vols.append((int(m.group(1)), p))
                files = [p for _, p in sorted(vols)]
        out = []
        for path in files:
            if not path.exists():
                continue
            _prefix, blocks = self._parse_plots(path.read_text(encoding="utf-8"))
            for seq, title, raw in blocks:
                out.append(self._parse_plot_block(seq, title, raw))
        return sorted(out, key=lambda p: p["seq"])

    def upsert_plots(self, vol: int, plots: list[dict]) -> int:
        """按 seq 合并进 outline/plots/vol-NN.md；未触碰条目逐字保留。"""
        path = self._vol_file(vol)
        with self._hold(path):
            if path.exists():
                prefix, blocks = self._parse_plots(path.read_text(encoding="utf-8"))
            else:
                prefix, blocks = f"# 第{vol}卷情节", []
            index = {b[0]: i for i, b in enumerate(blocks)}
            for plot in plots:
                seq = int(plot["seq"])
                rendered = self._render_plot(plot).split("\n")
                if seq in index:
                    _, title, _raw = blocks[index[seq]]
                    blocks[index[seq]] = (seq, str(plot.get("title") or title), rendered)
                else:
                    blocks.append((seq, str(plot.get("title") or ""), rendered))
            parts = [prefix] if prefix else []
            parts.extend("\n".join(raw).rstrip("\n") for _, _, raw in blocks)
            self._write(path, "\n\n".join(p for p in parts if p) + "\n")
        return len(plots)
