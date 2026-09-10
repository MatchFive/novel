"""只读取数据工具（聊天助手/编排 Agent 共用）：模型自主判断需要时读取项目数据。

标记协议：模型输出 [[READ:种类:参数]]（一行一个），由服务层执行并把结果回灌。
"""
from __future__ import annotations

import json as _json
import re

from app.db.session import Database

TOOL_PATTERN = re.compile(r"\[\[READ:([a-z_]+):([^\]]*)\]\]")

# 给模型的工具说明（写进聊天系统提示词）
TOOL_GUIDE_CHAT = (
    "【数据读取工具】当回答需要查看项目实际内容（章节正文/细纲/角色卡/设定全文/卷大纲）时，"
    "先只输出读取标记（一行一个，不要输出其他内容），系统会把数据给你，然后你再正式回答：\n"
    "[[READ:chapters:]] 章节列表（序号/标题/状态/字数）\n"
    "[[READ:chapter_content:第1章或标题]] 该章正文全文\n"
    "[[READ:chapter_outline:第1章或标题]] 该章细纲（节拍+场景/结尾钩子等）\n"
    "[[READ:plots:]] 情节线列表（卷→情节→章节三层结构中的情节层）\n"
    "[[READ:plot:情节名或第N情节]] 该情节详情+旗下章节\n"
    "[[READ:character:角色名]] 角色卡完整档案\n"
    "[[READ:world:设定名]] 设定条目完整内容\n"
    "[[READ:arc:第1卷或卷名]] 卷大纲详情+卷内章节\n"
    "能直接回答的不要调用；读不到会返回提示，不要编造。"
)


def _decode_text(raw: str | None) -> str:
    """智能解码：处理数据库中可能存在的混合编码问题。
    
    数据库中部分文本（主要是早期插入的标题、角色名）使用 GBK 编码存储，
    而内容字段使用 UTF-8。此函数尝试自动检测并正确解码。
    """
    if raw is None:
        return ""
    
    # 如果已经是正常的 Unicode 字符串（没有乱码特征），直接返回
    # 乱码特征：连续出现 � 或类似 U+FFFD 的替换字符
    if "\ufffd" not in raw and "�" not in raw:
        # 进一步检查：是否包含明显的 UTF-8 被错误解码为 Latin-1 的特征
        # 例如：\xc3\xa9 这样的字节序列被显示为 Ã©
        return raw
    
    # 尝试修复编码：将乱码的 UTF-8 字符串重新编码为 latin1，然后用 gbk 解码
    # 这是因为 Python 的 str 在内存中是 Unicode，如果数据库驱动错误地将 GBK 字节
    # 当作 UTF-8 解码，会得到乱码。我们需要反向操作。
    try:
        # 方案1：尝试 gbk 解码（针对标题、角色名等短文本）
        # 先将 str 编码回 bytes（使用 latin1，因为它能保留 0x80-0xFF 的字节值）
        bytes_data = raw.encode('latin1')
        # 然后尝试用 gbk 解码
        decoded = bytes_data.decode('gbk')
        # 验证解码结果：如果解码后没有乱码，且长度合理，则接受
        if "\ufffd" not in decoded and "�" not in decoded and len(decoded) > 0:
            return decoded
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    
    # 方案2：如果 gbk 失败，返回原始文本（可能已经可读或无法修复）
    return raw


def _decode_json_field(raw: str | list | dict | None) -> str | list | dict | None:
    """解码 JSON 字段中的文本，递归处理嵌套结构。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return _decode_text(raw)
    if isinstance(raw, list):
        return [_decode_json_field(item) for item in raw]
    if isinstance(raw, dict):
        return {k: _decode_json_field(v) for k, v in raw.items()}
    return raw


def extract_read_calls(text: str) -> list[tuple[str, str]]:
    """从模型输出提取读取标记：[(kind, arg), ...]。"""
    return [(m.group(1), m.group(2).strip()) for m in TOOL_PATTERN.finditer(text or "")]


def is_read_only_output(text: str) -> bool:
    """该输出是否纯工具调用（去掉标记后基本没内容）。"""
    rest = TOOL_PATTERN.sub("", text or "").strip()
    return bool(extract_read_calls(text)) and len(rest) < 20


def run_read_tool(db: Database, project_id: int, kind: str, arg: str) -> str:
    """执行只读查询，返回文本结果。"""
    from app.db.repositories.chapter_repo import ArcRepository, ChapterRepository
    from app.db.repositories.character_repo import CharacterRepository
    from app.db.repositories.world_repo import WorldEntryRepository

    def _find_chapter(session, target: str):
        chapters = ChapterRepository(session).list_by_project(project_id)
        m = re.search(r"第?\s*(\d+)\s*章?", target or "")
        if m and 0 < int(m.group(1)) <= len(chapters):
            return chapters[int(m.group(1)) - 1]
        t = (target or "").replace(" ", "")
        for c in chapters:
            ct = _decode_text(c.title or "").replace(" ", "")
            if t and ct and (t in ct or ct in t):
                return c
        return None

    try:
        with db.session_ctx() as session:
            if kind == "chapters":
                chapters = ChapterRepository(session).list_by_project(project_id)
                if not chapters:
                    return "（当前没有任何章节）"
                return "\n".join(
                    f"第{c.seq + 1}章「{_decode_text(c.title or '')}」｜{c.content_status}｜{c.word_count or 0} 字"
                    for c in chapters[:50])
            if kind == "plots":
                from app.db.repositories.chapter_repo import PlotRepository
                plots = PlotRepository(session).list_by_project(project_id)
                if not plots:
                    return "（当前没有任何情节）"
                return "\n".join(
                    f"情节{p.seq + 1}「{_decode_text(p.title or '')}」｜{p.status}｜"
                    f"{_decode_text(p.summary or '')[:60]}"
                    for p in plots[:50])
            if kind == "plot":
                from app.db.repositories.chapter_repo import PlotRepository
                plots = PlotRepository(session).list_by_project(project_id)
                plot = None
                m = re.search(r"第?\s*(\d+)\s*(?:情节|个情节)?", arg or "")
                if m and 0 < int(m.group(1)) <= len(plots):
                    plot = plots[int(m.group(1)) - 1]
                else:
                    t = (arg or "").replace(" ", "")
                    for p in plots:
                        pt = _decode_text(p.title or "").replace(" ", "")
                        if t and pt and (t in pt or pt in t):
                            plot = p
                            break
                if plot is None:
                    return f"（未找到情节「{arg}」）"
                chs = ChapterRepository(session).list_by_plot(plot.id)
                ch_list = "；".join(f"第{c.seq + 1}章「{_decode_text(c.title or '')}」" for c in chs) or "（无章节）"
                beats = plot.beats_json or []
                beats_text = "\n".join(
                    f"  {i + 1}. {b.get('event', '') if isinstance(b, dict) else b}"
                    for i, b in enumerate(beats)) or "（无节拍线）"
                return (f"情节{plot.seq + 1}「{_decode_text(plot.title or '')}」\n"
                        f"概要：{_decode_text(plot.summary or '')}\n"
                        f"核心冲突：{_decode_text(plot.conflict or '')}\n"
                        f"收束：{_decode_text(plot.resolution or '')}\n"
                        f"情节节拍线：\n{beats_text}\n"
                        f"旗下章节：{ch_list}")
            if kind == "chapter_content":
                ch = _find_chapter(session, arg)
                if ch is None:
                    return f"（未找到章节「{arg}」）"
                content = _decode_text(ch.content or "").strip()
                return (f"第{ch.seq + 1}章「{_decode_text(ch.title or '')}」\n"
                        + (content[:10000] if content else "（本章还没有正文）"))
            if kind == "chapter_outline":
                ch = _find_chapter(session, arg)
                if ch is None:
                    return f"（未找到章节「{arg}」）"
                # 解码 beats_json 中的文本
                beats_json = _decode_json_field(ch.beats_json)
                beats = "\n".join(
                    f"  {i + 1}. {b.get('pov', '')}｜{b.get('location', '')}｜{b.get('event', '')}"
                    for i, b in enumerate(beats_json or [])) or "（无细纲节拍）"
                outline_json = _decode_json_field(ch.outline_json)
                return (f"第{ch.seq + 1}章「{_decode_text(ch.title or '')}」\n目标：{_decode_text(ch.objective or '（无）')}\n"
                        f"细纲字段：{_json.dumps(outline_json or {}, ensure_ascii=False)[:800]}\n"
                        f"节拍：\n{beats}")
            if kind == "character":
                chars = CharacterRepository(session).list_by_role(project_id)
                t = arg.replace(" ", "")
                for c in chars:
                    cn = _decode_text(c.name or "").replace(" ", "")
                    ca = _decode_text(c.aliases or "").replace(" ", "")
                    if t and (t in cn or cn in t or t in ca):
                        profile = _decode_json_field(c.profile)
                        return f"「{cn}」（{c.role_type or '?'}）\n" + _json.dumps(
                            profile or {}, ensure_ascii=False)[:2000]
                return f"（未找到角色「{arg}」）"
            if kind == "world":
                entries = WorldEntryRepository(session).list_by_category(project_id, None)
                t = arg.replace(" ", "")
                for e in entries:
                    et = _decode_text(e.title or "").replace(" ", "")
                    if t and et and (t in et or et in t):
                        return f"「{_decode_text(e.title)}」\n{_decode_text(e.content or '')[:2000]}"
                return f"（未找到设定「{arg}」）"
            if kind == "arc":
                arcs = ArcRepository(session).list_by_project(project_id)
                arc = None
                m = re.search(r"第\s*(\d+)\s*卷", arg or "")
                if m and 0 < int(m.group(1)) <= len(arcs):
                    arc = arcs[int(m.group(1)) - 1]
                else:
                    t = (arg or "").replace(" ", "")
                    for a in arcs:
                        at = _decode_text(a.title or "").replace(" ", "")
                        if t and at and (t in at or at in t):
                            arc = a
                            break
                if arc is None:
                    return f"（未找到卷「{arg}」）"
                chs = ChapterRepository(session).list_by_arc(arc.id)
                ch_list = "；".join(f"第{c.seq + 1}章「{_decode_text(c.title or '')}」" for c in chs) or "（无章节）"
                return (f"第{arc.seq + 1}卷「{_decode_text(arc.title or '')}」\n目标：{_decode_text(arc.goal or '')}\n"
                        f"核心冲突：{_decode_text(arc.conflict or '')}\n收束：{_decode_text(arc.resolution or '')}\n"
                        f"概要：{_decode_text(arc.summary or '')[:1000]}\n卷内章节：{ch_list}")
            return f"（未知的读取种类: {kind}）"
    except Exception as exc:
        return f"（读取失败: {exc}）"
