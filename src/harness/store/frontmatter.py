"""章节文件的 front-matter 容错编解码。

ProjectStore（Task 5）只通过这里的三个入口读写 front-matter：
- `split_document`：纯文本拆成 (meta, body)；无 front-matter 返回 ({}, 全文)；
- `split_file`：读文件 + split_document，解析失败**先备份** `path`+`.bak` 再抛
  `FileFormatError`（绝不静默覆盖原文件）；
- `render_document`：meta + body 渲染回文件文本（body 先过 `strip_edges`）。

正文边界 = front-matter 结束 `---` 之后第一个**非空**行起（`---` 后的空行全部跳过
作分隔，正文内部一字不动）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from harness.text import strip_edges

__all__ = ["FileFormatError", "render_document", "split_document", "split_file"]


class FileFormatError(Exception):
    """章节文件解析失败（坏 YAML / meta 不是键值映射）。

    `split_file` 在抛此错前已把原文件复制为 `path`+`.bak`，原文件不被改动。
    """

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def _parse_frontmatter(lines: list[str]) -> tuple[dict, list[str]]:
    """lines[0] 是起始 `---`：找结束 `---`，返回 (meta, 结束行之后的剩余行)。

    坏 YAML 抛 yaml.YAMLError；meta 不是映射（含标量/列表）抛 ValueError——
    两种错都由 split_file 层备份 + 包成 FileFormatError。
    """
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("front-matter 缺少结束 ---")
    meta = yaml.load("\n".join(lines[1:end]) + "\n", Loader=yaml.SafeLoader)
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError(  # noqa: TRY004 - 有意用 ValueError：格式契约，由 split_file 统一包成 FileFormatError
            f"front-matter meta 不是键值映射: {type(meta).__name__}")
    return meta, lines[end + 1:]


def split_document(text: str) -> tuple[dict, str]:
    """把章节文本拆成 (meta, body)。

    以 `---` 第一行起头的按 front-matter 解析；否则返回 ({}, 全文)。
    body = 结束 `---` 之后第一个非空行起的全部内容（保留原文，不做内部处理）。
    """
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    meta, rest = _parse_frontmatter(lines)
    # 正文边界：跳过结束 --- 之后、第一个非空行之前的空行
    start = 0
    while start < len(rest) and rest[start].strip() == "":
        start += 1
    return meta, "\n".join(rest[start:])


def split_file(path: Path) -> tuple[dict, str]:
    """读章节文件并拆分；解析失败先备份 `path`+`.bak` 再抛 FileFormatError。"""
    text = Path(path).read_text(encoding="utf-8")
    try:
        return split_document(text)
    except (yaml.YAMLError, ValueError) as exc:
        backup = Path(str(path) + ".bak")
        shutil.copy2(path, backup)
        raise FileFormatError(Path(path), f"front-matter 解析失败: {exc}") from exc


def render_document(meta: dict, body: str) -> str:
    """(meta, body) 渲染回章节文件文本；写盘前 body 过 strip_edges。"""
    return f"---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)}---\n\n{strip_edges(body)}"
