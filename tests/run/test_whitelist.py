import pytest

from harness.run.whitelist import DELIVERABLE_TOOLS, TOOLS_ALL, allowed_for_run


def test_unknown_deliverable_raises():
    with pytest.raises(ValueError):
        allowed_for_run(["outline", "bogus"])


def test_union_with_dependency_closure():
    got = allowed_for_run(["chapters"])
    assert "plan_arcs" in got and "edit_by_command" in got


def test_discuss_is_read_only_plus_finish():
    got = allowed_for_run(["discuss"])
    assert "edit_by_command" not in got and "plan_arcs" not in got
    assert "ask_user" in got and "finish" in got


def test_every_mapped_tool_exists_in_tools_all():
    for tools in DELIVERABLE_TOOLS.values():
        assert tools <= TOOLS_ALL


def test_draft_includes_write_plot_excludes_planning():
    """P3 写作链：draft 集 = TOOLS_ALL−三规划工具（派生口径），自动含 write_plot；
    三个规划工具仍被排除（正文阶段只改不规划）。注意 allowed_for_run 走依赖闭包
    （draft∪chapters），规划工具的排除钉在 DELIVERABLE_TOOLS 映射上。"""
    draft_tools = DELIVERABLE_TOOLS["draft"]
    assert "write_plot" in draft_tools
    for planning in ("plan_arcs", "master_plan", "plan_chapters"):
        assert planning not in draft_tools
    assert "write_plot" in allowed_for_run(["draft"])
    assert "write_plot" in TOOLS_ALL and "write_plot" in DELIVERABLE_TOOLS["world"]


def test_deliverable_mappings_exact_sets():
    """T7 钉死映射集合精确相等（fail-closed 结构性保证的延续）：
    outline/chapters 含全部规划链工具；draft 排除 plan_arcs/master_plan/plan_chapters
    三个规划工具；world/discuss 口径不变。
    P6 起：check_plots / global_check（验收链写工具）进 write_flow 与 TOOLS_ALL 派生集。
    Task 17 起：draft_character（角色卡起草，只起草不落卡）进 write_flow 与 TOOLS_ALL。
    P22/A6：characters（设计/修订角色卡）= 只读集 + draft_character + edit_by_command
    + 问/收尾流程——**不含** write_plot 与规划链三工具（角色 run 不写正文、不规划）。
    P23：update_character / delete_character（修卡工具）进 _WRITE_FLOW → 全部集获得。
    P24：build_characters（自动建卡司，规划+起草+确认门落卡）进 _WRITE_FLOW 与
    characters 映射。"""
    readonly = frozenset({"get_project_state", "list_characters", "get_character",
                          "list_world", "get_world", "search_chapters",
                          "list_skills", "read_skill"})
    write_flow = frozenset({"edit_by_command", "write_plot", "resequence_chapters",
                            "extract_memory", "web_search",
                            "check_plots", "global_check", "draft_character",
                            "update_character", "delete_character", "build_characters",
                            "request_plan_approval",
                            "ask_user", "set_step_plan", "complete_step", "finish", "pause"})
    assert DELIVERABLE_TOOLS["world"] == readonly | write_flow
    assert DELIVERABLE_TOOLS["outline"] == frozenset(TOOLS_ALL)
    assert DELIVERABLE_TOOLS["chapters"] == frozenset(TOOLS_ALL)
    assert DELIVERABLE_TOOLS["draft"] == \
        frozenset(TOOLS_ALL - {"plan_arcs", "master_plan", "plan_chapters"})
    assert DELIVERABLE_TOOLS["discuss"] == readonly | frozenset({"ask_user", "finish", "pause"})
    assert DELIVERABLE_TOOLS["characters"] == \
        readonly | frozenset({"draft_character", "edit_by_command",
                              "update_character", "delete_character", "build_characters",
                              "ask_user", "finish", "pause"})


def test_characters_deliverable_semantics():
    """P22/A6：``characters`` run 能起草（draft_character）/修订（edit_by_command）
    角色卡，能问能收尾；**不能**写正文（write_plot）、不能规划（三规划工具）。"""
    got = allowed_for_run(["characters"])
    assert "draft_character" in got and "edit_by_command" in got
    assert "ask_user" in got and "finish" in got and "pause" in got
    assert "write_plot" not in got
    for planning in ("plan_arcs", "master_plan", "plan_chapters"):
        assert planning not in got


def test_check_tools_are_write_flow_only():
    """P6 验收链工具：进 TOOLS_ALL 与写流程集；discuss（只读集）不含它们。"""
    check_tools = frozenset({"check_plots", "global_check"})

    assert check_tools <= TOOLS_ALL
    assert check_tools <= allowed_for_run(["draft"])
    assert check_tools <= allowed_for_run(["outline"])
    assert not check_tools & allowed_for_run(["discuss"])     # 写工具不进只读集
