"""deliverables → 可用工具 fail-closed 白名单（设计 §1.2 附录 A 白名单）。

多选 deliverables 按「并集 + 依赖闭包」派生可用工具集；未知 deliverable 响亮失败（ValueError）。
纯模块：无 store、无 I/O。
"""

# P1+P2+P3 工具全集（附录 A 的 P1 子集 + P2 新增 + P3 新增 write_plot，回写附录 A 时注明）。
# draft 排除三个规划工具（plan_arcs/master_plan/plan_chapters）——正文阶段只改不规划；
# write_plot 经 TOOLS_ALL 派生自动进 draft/chapters/outline。
# P4：search_chapters（正文回指检索，只读零 LLM）/ resequence_chapters（章号重排，
# 联动 MemoryStore.rename_chapter_seq——裁定 8）。
# P5：extract_memory（人工改正文后重提取，幂等先清后建——写 memory/ 与 state.md）。
# P5：list_skills / read_skill（skills 体系只读工具，零 LLM）。
# P6：check_plots / global_check（验收链工具——落 issues + 调 LLM，写工具）。
TOOLS_ALL: frozenset[str] = frozenset(
    {
        "get_project_state",
        "list_characters",
        "get_character",
        "list_world",
        "get_world",
        "plan_arcs",
        "master_plan",
        "plan_chapters",
        "edit_by_command",
        "write_plot",
        "resequence_chapters",
        "search_chapters",
        "extract_memory",
        "list_skills",
        "read_skill",
        "web_search",
        "check_plots",
        "global_check",
        "draft_character",
        "update_character",
        "delete_character",
        "build_characters",
        "request_plan_approval",
        "ask_user",
        "set_step_plan",
        "complete_step",
        "finish",
        "pause",
    }
)

# 只读集（任意范围可用）
_READONLY: frozenset[str] = frozenset(
    {"get_project_state", "list_characters", "get_character", "list_world",
     "get_world", "search_chapters", "list_skills", "read_skill"}
)

# 写/流程控制集（build_world / build_characters 属 P3，落地时必须回来扩这张表）
_WRITE_FLOW: frozenset[str] = frozenset(
    {
        "edit_by_command",
        "write_plot",
        "resequence_chapters",
        "extract_memory",
        "web_search",
        "check_plots",
        "global_check",
        "draft_character",
        "update_character",
        "delete_character",
        "build_characters",
        "request_plan_approval",
        "ask_user",
        "set_step_plan",
        "complete_step",
        "finish",
        "pause",
    }
)

DELIVERABLE_TOOLS: dict[str, frozenset[str]] = {
    "world": _READONLY | _WRITE_FLOW,
    "outline": frozenset(TOOLS_ALL),
    "chapters": frozenset(TOOLS_ALL),
    "draft": frozenset(TOOLS_ALL - {"plan_arcs", "master_plan", "plan_chapters"}),
    "discuss": _READONLY | frozenset({"ask_user", "finish", "pause"}),
    # P22/A6：seed 词表里的 characters（设计/修订角色卡）。draft_character 起草 +
    # edit_by_command 修订角色卡 + 问/收尾；不写正文、不规划（白名单注释里「落地时
    # 必须回来扩这张表」的欠账，随 D2 角色卡子系统落地补上）。
    # P23：update_character / delete_character（修卡/删卡）同属角色卡维护。
    # P24：build_characters 自动建卡司（规划+起草+确认门落卡）；亦在 _WRITE_FLOW，
    # 规划期其它 deliverable（world/outline/...）并集获得。
    "characters": _READONLY | frozenset({"draft_character", "edit_by_command",
                                         "update_character", "delete_character",
                                         "build_characters",
                                         "ask_user", "finish", "pause"}),
}

# 依赖闭包：chapters 依赖 outline 产物；draft 依赖 chapters 产物（传递）
DEPENDENCY_CLOSURE: dict[str, frozenset[str]] = {
    "chapters": frozenset({"outline"}),
    "draft": frozenset({"chapters"}),
}


def allowed_for_run(deliverables: list[str]) -> frozenset[str]:
    """deliverables 多选 → 可用工具白名单（并集 + 依赖闭包传递）。

    未知 deliverable → ValueError（fail-closed 响亮失败）。
    """
    unknown = [d for d in deliverables if d not in DELIVERABLE_TOOLS]
    if unknown:
        raise ValueError(f"未知 deliverable: {unknown}（已知: {sorted(DELIVERABLE_TOOLS)}）")

    resolved: set[str] = set()
    stack = list(deliverables)
    while stack:
        d = stack.pop()
        if d in resolved:
            continue
        resolved.add(d)
        stack.extend(DEPENDENCY_CLOSURE.get(d, ()))

    allowed: set[str] = set()
    for d in resolved:
        allowed |= DELIVERABLE_TOOLS[d]
    return frozenset(allowed)
