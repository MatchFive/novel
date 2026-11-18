"""提示词加载器：项目级 > 用户级 > 内置 三级查找，{{var}} 严格渲染。

另提供 :func:`insert_before_output_section`——把**代码生成的附加段**插到输出契约段之前
（见该函数 docstring 的理由）。
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

#: 输出契约段的起始标记（生成类模板用它把附加段插到**输出契约之前**）
OUTPUT_SECTION_MARK = "【输出格式】"

#: HTML 注释（模板的文档注释块）
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class PromptRenderError(KeyError):
    """渲染失败：消息含 prompt key 与缺失变量名。"""


class PromptLoader:
    def __init__(self, project_root: Path | None = None,
                 home_dir: Path | None = None) -> None:
        self._project_root = Path(project_root) if project_root is not None else None
        self._home_dir = Path(home_dir) if home_dir is not None else Path.home()

    def get(self, key: str) -> str:
        candidates: list[Path] = []
        if self._project_root is not None:
            candidates.append(self._project_root / ".harness" / "prompts" / f"{key}.md")
        candidates.append(self._home_dir / ".harness" / "prompts" / f"{key}.md")
        for path in candidates:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        try:
            ref = resources.files("harness.prompts").joinpath("builtin").joinpath(f"{key}.md")
            return ref.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise KeyError(key) from None

    def render(self, key: str, **vars: str) -> str:
        template = self.get(key)

        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            if var not in vars:
                raise PromptRenderError(f"{key}: 缺失变量 {var}")
            return vars[var]

        return _VAR_RE.sub(replace, template)

    def render_with_blocks(self, key: str, vars: dict, blocks: list[str]) -> str:
        rendered = self.render(key, **vars)
        if not blocks:
            return rendered
        return rendered + "\n\n" + "\n\n".join(blocks)


def output_section_index(text: str) -> int:
    """真【输出格式】段的位置（找不到 → -1）。

    **必须跳过 HTML 注释**：模板的文档注释里常**提到**这个标记——
    ``task.auto_plan_plots`` 的注释就写着「输出契约（…）：同模板【输出格式】（plots +
    characters + need_web）」，那处命中会把附加段插进**注释内部**（独立复审 D1 实测）。
    同一函数在两个注入点上行为不一致（``task.auto_master_plan`` 只有一处命中，故正常）。

    取**最后一个**不在注释里的匹配：输出契约在模板正文末尾，而注释在开头。
    """
    spans = [(m.start(), m.end()) for m in _COMMENT_RE.finditer(text)]
    idx = -1
    for m in re.finditer(re.escape(OUTPUT_SECTION_MARK), text):
        if any(s <= m.start() < e for s, e in spans):
            continue                       # 注释里的提及 → 不是输出契约段
        idx = m.start()
    return idx


def insert_before_output_section(rendered: str, block: str) -> str:
    """把 ``block`` 插到**输出契约段之前**；模板无该段 → 追加到末尾。

    **为什么**：结构化输出的提示词里，「输出契约」应当在**最后**——模型最后读到的指令
    决定它吐什么。本仓的工具提示词是**单条消息**（没有 system/user 分段），代码生成的
    附加段若一律追加在末尾，就会把输出契约**顶到中间**。

    seed 不需要这个函数：它把这类「标准」段放在 **system 消息**里，输出契约在 user
    消息（天然最后）。本地没有 system 段 → 只能**手工插位**。

    标记找不到 → 安全降级为追加末尾（不抛错；模板改版也不会让工具挂掉）。
    """
    if not block:
        return rendered
    idx = output_section_index(rendered)
    if idx < 0:
        return rendered + "\n" + block
    return rendered[:idx] + block + "\n" + rendered[idx:]
