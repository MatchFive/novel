"""审计 issue 的「交给 AI 修」（P5 Task 5）：quote 定位 → consistency_fix → diff 确认卡
→ 落盘章正文 → 记忆重提取 → issue resolved。

链路（计划 Task 5 逐条兑现）：

1. **quote 定位**：``issue.chapter`` 有值先查该章，查不到再全章扫（正文被改过会让
   记录章号与实际落点漂移）；定位不到 → 如实说明「quote 已不可定位，issue 保持
   pending」，**不改状态**；**全局类意见（``quote`` 为空——跨章的结构性问题）另说**：
   它本来就没有可定位的原文片段，改写无从下手，如实说明「该意见是全局类（无原文
   片段），无法定位改写——issue 保持 pending」；
2. **上下文**：该章正文 + quote 起 ±500 字上下文；
3. **LLM 修复**：渲染 ``task.consistency_fix``（四变量同名）→ complete_structured
   （路由 standard/0.3/2048；**LLM 调用在 store 锁外**）→ JSON ``{"replacement": str}``；
   调用失败 / 解析失败 / 无 replacement → 观测如实、issue 保持 pending、不炸；
4. **diff 确认卡**：unified diff 预览（复用 ``tools/modify.py`` 的 ``_unified_diff``——
   不在本仓留第二份 diff 实现）→ ``ApprovalUI.ask`` 确认；取消 → 保持 pending、零写盘；
5. **落盘**：确认后**就地**改写章正文（只换定位到的那一段；文件名与 front-matter 原文
   逐字节保留）→ ``reextract_chapter``（Task 4 统一入口，幂等先清后建；**传定位到的那个
   章**——内部按 seq 解析章文件是按文件名前缀 glob，手写章会落到别的章上）
   → ``set_status(resolved)``；重提取失败不阻断（正文已改、issue 已处理），如实观测。

审计纪律的例外通道：审计本身「只报不改」，本函数是**人工终裁之后**才走的修复路径，
且改动范围严格限定在 quote 定位到的那一段（其余正文逐字不动）。

失败语义与 P4/P5 口径一致：观测里的失败原因一律过 ``harness.reasons.reason_label``
（禁瞬时词上屏）；本模块**不 raise**——修复失败一律收敛为观测文本 + issue 保持 pending。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from harness.memory.extract import reextract_chapter, reextract_failed, reextract_reason
from harness.prompts.loader import PromptLoader
from harness.reasons import reason_label
from harness.run.approval import ApprovalCancelled, ApprovalUI, ask_ui_async
from harness.run.issues import Issue, IssuesStore
from harness.store.frontmatter import FileFormatError, split_document
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import locate_quote
from harness.tools.modify import _unified_diff
from harness.writing.pure import WRITING_ROUTING

__all__ = ["fix_issue"]

log = logging.getLogger("audit.fix")

# quote 上下文半径（计划：quote 起 ±500 字）
_CONTEXT_RADIUS = 500

#: 行尾归一（\r\n / \r → \n）：与 ``Path.read_text`` 的通用换行口径一致
_NEWLINE_RE = re.compile(r"\r\n|\r")


def _warn_broken(path, exc: FileFormatError) -> None:
    """坏章只跳过该章：log.warning 点名文件 + 原因，其余章照常参与定位。"""
    log.warning("修复跳过坏章 %s: %s", path, exc.reason)


def _locate_quote(store: ProjectStore, issue: Issue) -> tuple[ChapterDoc, int, int] | None:
    """quote 定位 → (章文档, 起点, 终点)；定位不到返回 None。

    ``issue.chapter`` 有值先查该章（正常路径）；查不到再全章扫——正文被人工改过会让
    issue 记录的章号与 quote 实际落点漂移，一处不中不等于全局不中。
    """
    quote = (issue.quote or "").strip()
    if not quote:
        return None
    docs = {d.seq: d for d in store.list_chapters(on_error=_warn_broken)}
    order: list[ChapterDoc] = []
    if issue.chapter is not None and issue.chapter in docs:
        order.append(docs[issue.chapter])
    order.extend(d for seq, d in sorted(docs.items()) if seq != issue.chapter)
    for doc in order:
        pos = locate_quote(doc.body, quote)
        if pos is not None:
            return doc, pos[0], pos[1]
    return None


def _extract_replacement(text: str) -> object | None:
    """结构化输出解析：``{"replacement": 非空 str}`` → 该 dict；其余形态 → None。

    None 交 ``complete_structured`` 的扩额阶梯处理（两轮仍失败 → bad_json）。
    惰性 import ``tools.base``（同 review.py：tools 包顶层 import 链长，顶层再 import 易成环）。
    """
    from harness.tools.base import extract_json
    value = extract_json(text)
    if not isinstance(value, dict):
        return None
    replacement = value.get("replacement")
    if not isinstance(replacement, str) or not replacement.strip():
        return None
    return value


# ------------------------------------------------------------------ 就地改写（I-1）

def _body_region(raw: str) -> tuple[str, int] | None:
    """原始文件文本 → (正文原文, 正文在 raw 中的起始偏移)；无法定位 → None。

    与 ``store.frontmatter.split_document`` 同口径：front-matter 结束 ``---`` 之后
    第一个非空行起是正文；无 front-matter 时全文即正文。**行尾（\\r\\n）原样保留**。
    """
    lines: list[str] = []
    offsets: list[int] = []
    pos = 0
    for part in raw.split("\n"):
        lines.append(part)
        offsets.append(pos)
        pos += len(part) + 1
    if not lines or lines[0].strip() != "---":
        return raw, 0
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    start = end + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        return "", len(raw)
    off = offsets[start]
    return raw[off:], off


def _norm_to_raw(raw: str, norm_off: int) -> int:
    """归一（\\n 口径）偏移 → 原始文本偏移（保留原行尾 \\r\\n）。

    ``doc.body`` 的偏移是 \\n 口径（``read_text`` 通用换行），盘上原文可能是 \\r\\n
    → **不能**用裸字符偏移拼回去，必须逐字符换算。
    """
    i = 0
    n = 0
    while n < norm_off and i < len(raw):
        if raw[i] == "\r" and i + 1 < len(raw) and raw[i + 1] == "\n":
            i += 2
        else:
            i += 1
        n += 1
    return i


def _fallback_chapter_path(store: ProjectStore, doc: ChapterDoc) -> Path | None:
    """无 ``path`` 的手工构造章 → 按 seq glob 候选文件（**可能命中别的章**）。

    失败关闭：glob 按的是**文件名前缀**，``doc.seq`` 是内存里的章号——两者不一致时
    glob 命中的是别的章（改它就是改错章）。候选文件的 front-matter 章号必须与
    ``doc.seq`` 一致才认；不一致（或章号读不出来）→ 返回 None（宁可不改）。
    """
    path = store.chapter_file_path(doc.seq)
    if path is None:
        return None
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        meta, _body = split_document(raw)
    except (yaml.YAMLError, ValueError):
        return None
    seq = meta.get("seq")
    if seq is None:      # 没写章号 → 无从判别，交给正文比对那道闸
        return path
    try:
        return path if int(seq) == doc.seq else None
    except (TypeError, ValueError):
        return None


def _rewrite_in_place(store: ProjectStore, doc: ChapterDoc, start: int, end: int,
                      replacement: str) -> bool:
    """**就地**改写章正文：读原文件字节 → 只替换正文中定位到的那一段 → 写回同一路径。

    文件名与 front-matter 原文**逐字节保留**（不经 ``write_chapter``：它按 title 重命名
    文件、删原文件、重新序列化 front-matter、裁正文首尾空白——对手写章是破坏性的）。
    返回 False = 定位不到盘上原文（调用方如实说明，保持 pending，绝不猜着改）。

    **失败关闭**两闸：候选文件的 front-matter 章号须与 ``doc.seq`` 一致（回退路径
    按文件名前缀 glob，可能命中别的章）；盘上正文须与 ``doc.body`` 一致（正文被改过 /
    文件被换过）。任一不符 → 一个字节都不写。
    """
    path = doc.path if doc.path is not None else _fallback_chapter_path(store, doc)
    if path is None:
        return False
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("章文件读取失败（跳过改写）：%s", exc)
        return False
    region = _body_region(raw)
    if region is None:
        return False
    body_raw, body_off = region
    if _NEWLINE_RE.sub("\n", body_raw) != doc.body:
        # 盘上正文与内存里的 doc 不一致（正文被改过 / 文件被换过）→ 不猜、不改
        log.warning("章文件正文与读取时不一致（跳过改写）：%s", path)
        return False
    raw_start = _norm_to_raw(body_raw, start)
    raw_end = _norm_to_raw(body_raw, end)
    text = replacement
    if "\r\n" in body_raw:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")   # 跟随原行尾风格
    new_body = body_raw[:raw_start] + text + body_raw[raw_end:]
    path.write_bytes((raw[:body_off] + new_body).encode("utf-8"))
    return True


def _rewrite_spans_in_place(store: ProjectStore, doc: ChapterDoc,
                            spans: list[tuple[int, int, str]]) -> bool:
    """**一次写盘**地改写章正文的**若干段**（spans 互不重叠，正文偏移 \\n 口径、升序）。

    与 :func:`_rewrite_in_place`（单段）**同一套失败关闭闸**：候选文件的 front-matter
    章号须与 ``doc.seq`` 一致（回退路径按文件名前缀 glob，可能命中别的章）；盘上正文须与
    ``doc.body`` 一致（正文被改过 / 文件被换过）。任一不符 → 一个字节都不写。

    **为什么要「多段一次写」**：P8 的「一章一次改写」契约是**一次写盘**。逐段写 N 次时，
    中途崩溃（进程被杀 / 断电）会留下**半改写**的章：已改的段进了盘、台账一条没动 →
    重跑时那些引文已定位不到 → 意见**永远卡在 pending**，人工再也用 ``/fix`` 处理不了
    （只能手工改）。一次写盘没有这个中间态（独立复审 D8）。

    逐字节保留：只动命中的那些段，盘上其余部分（front-matter、行尾风格、其余正文）
    **原样写回**——与单段版的口径一致。
    """
    if not spans:
        return False
    path = doc.path if doc.path is not None else _fallback_chapter_path(store, doc)
    if path is None:
        return False
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("章文件读取失败（跳过改写）：%s", exc)
        return False
    region = _body_region(raw)
    if region is None:
        return False
    body_raw, body_off = region
    if _NEWLINE_RE.sub("\n", body_raw) != doc.body:
        # 盘上正文与内存里的 doc 不一致（正文被改过 / 文件被换过）→ 不猜、不改
        log.warning("章文件正文与读取时不一致（跳过改写）：%s", path)
        return False
    # 归一偏移 → 原始偏移（盘上可能是 \r\n，不能用裸字符偏移）
    mapped: list[tuple[int, int, str]] = []
    for start, end, repl in spans:
        raw_start = _norm_to_raw(body_raw, start)
        raw_end = _norm_to_raw(body_raw, end)
        text = repl
        if "\r\n" in body_raw:
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")   # 跟随原行尾风格
        mapped.append((raw_start, raw_end, text))
    # 从后往前拼（靠前一段的偏移不受后面影响）——**一次**写回
    new_body = body_raw
    for raw_start, raw_end, text in sorted(mapped, key=lambda s: s[0], reverse=True):
        new_body = new_body[:raw_start] + text + new_body[raw_end:]
    path.write_bytes((raw[:body_off] + new_body).encode("utf-8"))
    return True


async def fix_issue(*, store: ProjectStore, mem, llm, prompts: PromptLoader, settings,
                    issue: Issue, emit=None, ui: ApprovalUI | None = None) -> str:
    """修复一条 audit issue（见模块 docstring）。返回观测文本。

    ``ui`` 为可选确认门注入点（默认自建 ``ApprovalUI()``；测试注入假 ask）。
    ``emit`` 为预留观测口——本轮**不新增事件 kind**（设计附录 E 未列 issue_fixed），
    与 ``reextract_chapter`` 的同款处理一致。
    """
    del emit  # 预留口：本轮无消费方（不新增事件 kind）
    ui = ui or ApprovalUI()

    # 两种「定位不到」要分开说：**全局类意见本来就没有 quote**（跨章的结构性问题，
    # 没有可定位的原文片段 → 改写无从下手），它与「有 quote 但正文里找不到」
    # （正文被改过 / 引文不准）是两回事——后者那句文案 P5 逐字钉死，不动。
    if not (issue.quote or "").strip():
        return "该意见是全局类（无原文片段），无法定位改写——issue 保持 pending"
    found = _locate_quote(store, issue)
    if found is None:
        return "quote 已不可定位，issue 保持 pending"
    doc, start, end = found
    quote = doc.body[start:end]
    context = doc.body[max(0, start - _CONTEXT_RADIUS):end + _CONTEXT_RADIUS]

    prompt = prompts.render("task.consistency_fix",
                            issue_title=issue.title or "",
                            issue_detail=issue.detail or "",
                            quote=quote,
                            context=context)
    provider = llm.provider
    tier, temp, mt = WRITING_ROUTING["task.consistency_fix"]
    model = getattr(provider, tier, None) or provider.standard

    # 惰性 import 同 _extract_replacement：顶层 import 易与 tools 包成环
    from harness.tools.base import complete_structured
    data, _usage, reason = await complete_structured(
        llm, model=model, temperature=temp, max_tokens=mt,
        prompt=prompt, extract=_extract_replacement)
    if data is None:
        return (f"修复失败（{reason_label(reason)}）：未产出可用的替换文本，"
                "issue 保持 pending")
    replacement = str(data["replacement"]).strip()

    new_body = doc.body[:start] + replacement + doc.body[end:]
    if new_body == doc.body:
        return "修复无变化：替换文本与原文一致，issue 保持 pending"

    diff = _unified_diff(f"chapters/{doc.seq:04d}.md", doc.body, new_body)
    question = (f"审计 issue {issue.id}：{issue.title}\n"
                f"待修复片段：{quote}\n\n{diff}\n\n确认应用？")
    try:
        choice = await ask_ui_async(ui, question, ["应用修复", "取消"], multi=False)
    except ApprovalCancelled:
        return "已取消，issue 保持 pending"
    if not choice.indices or choice.indices[0] != 0:
        return "已取消，issue 保持 pending"

    # 就地改写（先于内存态更新：_rewrite_in_place 要拿**原**正文与盘上比对）
    if not _rewrite_in_place(store, doc, start, end, replacement):
        return "章文件定位失败（未改写正文），issue 保持 pending"
    doc.body = new_body
    # 记忆重提取**按定位到的这一章**（doc=）：reextract_chapter 内部按 seq 解析章文件时
    # 是**按文件名前缀** glob（章号却取自 front-matter）——手写章两者不一致时会读到
    # 别的章 → 那一章的记忆被清掉重写、被改写的那章的记忆从不刷新。
    result = await reextract_chapter(store=store, mem=mem, llm=llm, prompts=prompts,
                                     settings=settings, seq=doc.seq, doc=doc)
    IssuesStore(store.root).set_status(issue.id, "resolved")
    if reextract_failed(result.text):
        # 重提取失败不阻断：正文已改、issue 已处理，如实观测失败原因
        return (f"已修改第{doc.seq}章并标记 resolved，但记忆重提取失败："
                f"{reextract_reason(result.text)}")
    return f"已修改第{doc.seq}章并标记 resolved，记忆已重提取"
