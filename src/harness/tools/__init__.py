"""工具层：注册表 + 只读工具集 + 规划/修改写工具。"""
from harness.skills import list_skills, read_skill
from harness.tools.chapters import plan_chapters, register_chapter_tools
from harness.tools.check import check_plots, global_check, register_check_tools
from harness.tools.memory import extract_memory, register_memory_tools
from harness.tools.modify import edit_by_command, register_modify_tools
from harness.tools.outline import plan_arcs, register_outline_tools
from harness.tools.plots import master_plan, register_plot_tools
from harness.tools.read import (
    READ_ONLY_TOOLS,
    get_character,
    get_project_state,
    get_world,
    list_characters,
    list_world,
)
from harness.tools.registry import ToolRegistry
from harness.tools.search import search_chapters
from harness.tools.web import register_web_tools, web_search
from harness.tools.writing import register_writing_tools, write_plot

__all__ = [
    "READ_ONLY_TOOLS",
    "ToolRegistry",
    "check_plots",
    "edit_by_command",
    "extract_memory",
    "get_character",
    "get_project_state",
    "get_world",
    "global_check",
    "list_characters",
    "list_skills",
    "list_world",
    "master_plan",
    "plan_arcs",
    "plan_chapters",
    "read_skill",
    "readonly_registry",
    "register_chapter_tools",
    "register_check_tools",
    "register_memory_tools",
    "register_modify_tools",
    "register_outline_tools",
    "register_plot_tools",
    "register_web_tools",
    "register_writing_tools",
    "search_chapters",
    "web_search",
    "write_plot",
]


def readonly_registry(store) -> ToolRegistry:
    """把全部只读工具注册进一张表（read_only=True），交给 ReAct loop 使用。

    P4 Task 8 起含 search_chapters（正文回指检索，零 LLM 的 registry 同步路径）。
    P5 Task 6 起含 list_skills / read_skill（skills 体系，零 LLM 只读）。
    """
    registry = ToolRegistry(store)
    for name, handler in READ_ONLY_TOOLS.items():
        registry.register(name, handler, read_only=True)
    registry.register("search_chapters", search_chapters, read_only=True)
    registry.register("list_skills", list_skills, read_only=True)
    registry.register("read_skill", read_skill, read_only=True)
    return registry
