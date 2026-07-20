"""多阶段章节生成 Worker 的 Prompt 模板。

所有模板返回字符串，要求 LLM 输出合法 JSON。
"""
from __future__ import annotations

import json
from string import Template


_JSON_RULES = """JSON 输出规则（必须遵守）：
1. 只返回纯 JSON，不要 markdown 代码块（如 ```json），不要解释。
2. action="update" 时必须提供 entity_id；action="add" 时 entity_id 必须为 null。
3. 所有字符串字段必须是合法 JSON 字符串（转义双引号、换行使用 \\n）。
"""


BROAD_OUTLINE_PROMPT_TEMPLATE = Template(
    """你是长篇小说总纲架构师。根据项目摘要、现有大纲、角色、世界观和剧情节点，生成或更新项目级总纲（type="broad"）。

${_json_rules}

输出格式：
{
  "changes": [
    {"action": "add|update", "entity_id": null或id, "temp_id": "temp:broad:1", "fields": {"title": "总纲标题", "content": "总纲正文", "type": "broad"}},
    {"action": "add", "temp_id": "temp:period:1", "fields": {"title": "时期标题", "content": "时期概述", "type": "period", "parent_id": "temp:broad:1"}},
    {"action": "add", "fields": {"title": "卷标题", "content": "卷内容", "type": "volume", "parent_id": "temp:period:1", "chapter_start": 1, "chapter_end": 10}}
  ]
}

业务规则：
- 若已有大纲中存在 type="broad" 的条目，应优先使用 action="update" 并填写其 id。
- content 应包含主线目标、核心冲突、关键转折、整体结构，300-800字。
- title 简洁，概括项目核心。
- 时期节点使用 type="period"，parent_id 指向总纲的 temp_id 或已有总纲 id。
- 卷节点使用 type="volume"，parent_id 指向所属时期的 temp_id 或已有时期 id，并填写 chapter_start/chapter_end（1-based，可为 null）。

输入数据：
【项目摘要】
$project_summary

【现有大纲】
$existing_outlines

【角色】
$characters

【世界观】
$world

【剧情节点】
$plot_nodes
"""
)

PLOT_NODES_PROMPT_TEMPLATE = Template(
    """你是长篇小说剧情节点设计师。根据总纲和现有剧情节点，抽取关键剧情节点（桥段/事件）。

${_json_rules}

输出格式：
{
  "changes": [
    {
      "action": "add|update",
      "entity_id": null或已有id,
      "fields": {
        "title": "节点标题",
        "summary": "节点摘要",
        "timeline_pos": "时间线位置"
      }
    }
  ]
}

业务规则：
- 不要遗漏总纲中的关键转折与高潮。
- timeline_pos 可为 "开篇"/"发展"/"高潮"/"结局" 或自定义位置描述。
- 若已有剧情节点与当前节点标题/摘要相似，应使用 action="update" 并复用其 id。

输入数据：
【总纲】
$broad_outline

【现有剧情节点】
$existing_plot_nodes

【角色】
$characters

【世界观】
$world
"""
)

ASSIGNMENT_PROMPT_TEMPLATE = Template(
    """你是长篇小说章节分配师。根据剧情节点与已有章节，将每个剧情节点分配到最合适的章节（必要时可新建占位章节）。

${_json_rules}

输出格式：
{
  "changes": [
    {
      "action": "add|update",
      "entity_type": "plot",
      "entity_id": "plot_id",
      "fields": {
        "chapter_id": "chapter_id",
        "order": 0
      }
    },
    {
      "action": "add|update",
      "entity_type": "chapter",
      "entity_id": null或已有id,
      "fields": {
        "title": "章节标题",
        "order": 0
      }
    }
  ]
}

业务规则：
- 对 plot 变更：entity_type="plot"，entity_id 为剧情节点 id，fields 包含 chapter_id（已存在的章节 id）与 order（在章节内的顺序，从 0 开始）。
- 对 chapter 变更：用于新建占位章节，entity_type="chapter"，entity_id 为 null 或已有章节 id，fields 包含 title 与 order。
- action="update" 时必须提供 entity_id。
- 不要修改已有章节的 content 或 detailed_outline。
- 一个章节可包含多个剧情节点，按 order 排序。
- 每章目标约 $target_words 字；按每 800-1000 字容纳一个剧情节点估算单章容量，单章节点过多时必须拆分到后续章节；已有章节容量不足时新建足量占位章节，避免一章剧情过密。

输入数据：
【剧情节点】
$plot_nodes

【已有章节】
$existing_chapters
"""
)

CHAPTER_OUTLINE_PROMPT_TEMPLATE = Template(
    """你是长篇小说章节细纲设计师。为目标章节生成详细的章节大纲（detailed_outline），并将 status 设为 "reviewed"。

${_json_rules}

输出格式：
{
  "changes": [
    {
      "action": "update",
      "entity_id": "chapter_id",
      "fields": {
        "detailed_outline": "...",
        "status": "reviewed"
      }
    }
  ]
}

业务规则：
- entity_id 必须为目标章节 id（即下方【目标章节】的 id）。
- detailed_outline 应包含：场景列表、本章目标、冲突、情感弧线、结尾钩子。
- 保持与总纲、剧情节点、角色、世界观一致。
- 参考前文摘要和活跃伏笔。
- 本章目标约 $target_words 字，细纲场景规模应与之匹配（场景数约为目标字数/600）。

输入数据：
【目标章节】
$chapter

【本卷大纲】
$volume_outline

【总纲】
$broad_outline

【分配到本章的剧情节点】
$assigned_plot_nodes

【角色】
$characters

【世界观】
$world

【前文摘要】
$previous_chapter_summary

【活跃伏笔】
$active_foreshadows
"""
)

CHAPTER_TEXT_PROMPT_TEMPLATE = Template(
    """你是长篇小说正文作者。根据细纲、剧情节点、角色、世界观、前文尾部和活跃伏笔，为指定章节生成完整正文。

${_json_rules}

输出格式：
{
  "changes": [
    {
      "action": "update",
      "entity_id": "chapter_id",
      "fields": {
        "content": "...",
        "status": "generated"
      }
    }
  ]
}

业务规则：
- entity_id 必须为目标章节 id（即下方【目标章节】的 id）。
- 保持人物一致性、伏笔呼应、节奏连贯。
- 如果【前文尾部】存在，请保持叙事衔接。
- 本章目标约 $target_words 字（允许 ±20% 浮动）。
- 正文将采用分段连续写作：每次调用只写一段，需与【上一段尾部】自然衔接（由用户消息提供）。

输入数据：
【目标章节】
$chapter

【本卷大纲】
$volume_outline

【细纲】
$detailed_outline

【分配到本章的剧情节点】
$assigned_plot_nodes

【角色】
$characters

【世界观】
$world

【前文尾部】
$previous_chapter_text_tail

【前章摘要链】
$previous_summaries

【活跃伏笔】
$active_foreshadows
"""
)

CHAPTER_REVIEW_PROMPT_TEMPLATE = Template(
    """你是小说审校员。检查以下章节正文是否存在物理逻辑、人物一致性或伏笔呼应问题。

${_json_rules}

输出格式：
- 若无问题：{"ok": true}
- 若有问题：{"ok": false, "issues": ["问题1", "问题2"]}

业务规则：
- 只关注 critical 问题（明显违反前文设定、伏笔未呼应、物理逻辑硬伤）。
- 文风、篇幅问题不列入 issues。

输入数据：
【章节信息】
$chapter

【正文】
$chapter_text

【角色】
$characters

【世界观】
$world

【活跃伏笔】
$active_foreshadows
"""
)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def BROAD_OUTLINE_PROMPT(
    project_summary: str,
    existing_outlines: list[dict],
    characters: list[dict],
    world: list[dict],
    plot_nodes: list[dict],
) -> str:
    return BROAD_OUTLINE_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        project_summary=project_summary,
        existing_outlines=_dumps(existing_outlines),
        characters=_dumps(characters),
        world=_dumps(world),
        plot_nodes=_dumps(plot_nodes),
    )


def PLOT_NODES_PROMPT(
    broad_outline: str,
    existing_plot_nodes: list[dict],
    characters: list[dict],
    world: list[dict],
) -> str:
    return PLOT_NODES_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        broad_outline=broad_outline,
        existing_plot_nodes=_dumps(existing_plot_nodes),
        characters=_dumps(characters),
        world=_dumps(world),
    )


def ASSIGNMENT_PROMPT(
    plot_nodes: list[dict],
    existing_chapters: list[dict],
    target_words: int = 2500,
) -> str:
    return ASSIGNMENT_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        plot_nodes=_dumps(plot_nodes),
        existing_chapters=_dumps(existing_chapters),
        target_words=target_words,
    )


def CHAPTER_OUTLINE_PROMPT(
    chapter: dict,
    broad_outline: str,
    assigned_plot_nodes: list[dict],
    characters: list[dict],
    world: list[dict],
    previous_chapter_summary: str,
    active_foreshadows: list[dict],
    target_words: int = 2500,
    volume_outline: str = "（暂无卷大纲）",
) -> str:
    return CHAPTER_OUTLINE_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        chapter=_dumps(chapter),
        volume_outline=volume_outline or "（暂无卷大纲）",
        broad_outline=broad_outline,
        assigned_plot_nodes=_dumps(assigned_plot_nodes),
        characters=_dumps(characters),
        world=_dumps(world),
        previous_chapter_summary=previous_chapter_summary or "（无）",
        active_foreshadows=_dumps(active_foreshadows),
        target_words=target_words,
    )


def CHAPTER_TEXT_PROMPT(
    chapter: dict,
    detailed_outline: str,
    assigned_plot_nodes: list[dict],
    characters: list[dict],
    world: list[dict],
    previous_chapter_text_tail: str,
    active_foreshadows: list[dict],
    previous_summaries: str = "（无）",
    target_words: int = 2500,
    volume_outline: str = "（暂无卷大纲）",
) -> str:
    return CHAPTER_TEXT_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        chapter=_dumps(chapter),
        volume_outline=volume_outline or "（暂无卷大纲）",
        detailed_outline=detailed_outline or "（无）",
        assigned_plot_nodes=_dumps(assigned_plot_nodes),
        characters=_dumps(characters),
        world=_dumps(world),
        previous_chapter_text_tail=previous_chapter_text_tail or "（无）",
        active_foreshadows=_dumps(active_foreshadows),
        previous_summaries=previous_summaries or "（无）",
        target_words=target_words,
    )


def CHAPTER_REVIEW_PROMPT(
    chapter_text: str,
    chapter: dict,
    characters: list[dict],
    world: list[dict],
    active_foreshadows: list[dict],
) -> str:
    return CHAPTER_REVIEW_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        chapter_text=chapter_text,
        chapter=_dumps(chapter),
        characters=_dumps(characters),
        world=_dumps(world),
        active_foreshadows=_dumps(active_foreshadows),
    )


# ---------------------------------------------------------------------------
# 兼容 brief 的 context 风格接口
# ---------------------------------------------------------------------------

def broad_outline_prompt(context: dict) -> str:
    """根据 context 生成/更新项目级总纲的 prompt。"""
    return BROAD_OUTLINE_PROMPT(
        project_summary=context.get("project_summary", "") or "未提供",
        existing_outlines=context.get("existing_outlines") or [],
        characters=context.get("characters") or [],
        world=context.get("world") or [],
        plot_nodes=context.get("plot_nodes") or [],
    )


def plot_nodes_prompt(context: dict) -> str:
    """根据 context 抽取关键剧情节点的 prompt。"""
    return PLOT_NODES_PROMPT(
        broad_outline=context.get("broad_outline", "") or "（暂无总纲）",
        existing_plot_nodes=context.get("existing_plot_nodes") or [],
        characters=context.get("characters") or [],
        world=context.get("world") or [],
    )


def assignment_prompt(context: dict) -> str:
    """根据 context 将剧情节点分配到章节的 prompt。"""
    return ASSIGNMENT_PROMPT(
        plot_nodes=context.get("plot_nodes") or [],
        existing_chapters=context.get("existing_chapters") or [],
        target_words=context.get("target_words") or 2500,
    )


def chapter_outline_prompt(context: dict) -> str:
    """根据 context 生成当前章节细纲的 prompt。"""
    return CHAPTER_OUTLINE_PROMPT(
        chapter=context["chapter"],
        broad_outline=context.get("broad_outline", "") or "（暂无总纲）",
        assigned_plot_nodes=context.get("assigned_plot_nodes") or [],
        characters=context.get("characters") or [],
        world=context.get("world") or [],
        previous_chapter_summary=context.get("previous_chapter_summary", "") or "（无）",
        active_foreshadows=context.get("active_foreshadows") or [],
        target_words=context.get("target_words") or 2500,
        volume_outline=context.get("volume_outline", "") or "（暂无卷大纲）",
    )


def chapter_text_prompt(context: dict) -> str:
    """根据 context 生成章节正文的 prompt。"""
    return CHAPTER_TEXT_PROMPT(
        chapter=context["chapter"],
        detailed_outline=context.get("detailed_outline", "") or "（无）",
        assigned_plot_nodes=context.get("assigned_plot_nodes") or [],
        characters=context.get("characters") or [],
        world=context.get("world") or [],
        previous_chapter_text_tail=context.get("previous_chapter_text_tail", "") or "（无）",
        active_foreshadows=context.get("active_foreshadows") or [],
        previous_summaries=context.get("previous_summaries", "") or "（无）",
        target_words=context.get("target_words") or 2500,
        volume_outline=context.get("volume_outline", "") or "（暂无卷大纲）",
    )


def chapter_review_prompt(context: dict) -> str:
    """根据 context 审校章节正文的 prompt。"""
    return CHAPTER_REVIEW_PROMPT(
        chapter_text=context.get("chapter_text", "") or "",
        chapter=context.get("chapter") or {},
        characters=context.get("characters") or [],
        world=context.get("world") or [],
        active_foreshadows=context.get("active_foreshadows") or [],
    )


RATING_LABELS = {"loose": "宽松", "standard": "标准", "strict": "严格"}

CHAPTER_RATING_PROMPT_TEMPLATE = Template(
    """你是网络小说内容尺度审校员。请按指定的尺度等级检查章节正文。

${_json_rules}

当前尺度等级：$rating_label
- loose（宽松）：仅拦截违法与极端内容（未成年人相关内容、教唆犯罪等），其余放行。
- standard（标准）：允许紧张暴力与含蓄亲密描写；不允许露骨性描写、细致酷刑与血腥渲染。
- strict（严格）：不允许明确性描写与露骨血腥；亲密、暴力仅可暗示性带过。

输出格式：
- 若无问题：{"ok": true}
- 若有问题：{"ok": false, "issues": [{"excerpt": "问题段落摘录（50字内）", "problem": "问题描述", "suggestion": "改写建议"}]}

业务规则：
- 只列出超出当前等级的内容，不要评论文风、逻辑或篇幅问题。
- 拿不准的放行。

【章节正文】
$chapter_text
"""
)


def CHAPTER_RATING_PROMPT(chapter_text: str, rating: str) -> str:
    return CHAPTER_RATING_PROMPT_TEMPLATE.substitute(
        _json_rules=_JSON_RULES,
        rating_label=RATING_LABELS.get(rating, "标准"),
        chapter_text=chapter_text,
    )


def chapter_rating_prompt(context: dict) -> str:
    """根据 context 生成尺度审校 prompt。context 键：chapter_text、rating。"""
    return CHAPTER_RATING_PROMPT(
        chapter_text=context.get("chapter_text", "") or "",
        rating=context.get("rating", "standard"),
    )


CHAPTER_SEGMENT_USER_TEMPLATE = Template(
    """【写作进度】
本章目标总字数：$target_words（允许 ±20% 浮动）
已完成字数：$accumulated_words
当前为第 $segment_index 段

【上一段尾部】
$prev_segment_tail

请撰写下一段正文（800-1200 字），与上一段自然衔接；若本章细纲内容已全部写完，输出 finished=true。
只输出 JSON：{"text": "本段正文", "finished": false}"""
)


def chapter_segment_user_prompt(
    segment_index: int,
    accumulated_words: int,
    target_words: int,
    prev_segment_tail: str,
) -> str:
    return CHAPTER_SEGMENT_USER_TEMPLATE.substitute(
        segment_index=segment_index,
        accumulated_words=accumulated_words,
        target_words=target_words,
        prev_segment_tail=prev_segment_tail or "（第一段，从头开始）",
    )
