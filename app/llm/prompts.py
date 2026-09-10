"""Prompt 模板渲染：纯函数，不触碰数据库（模板读取由调用方经仓储层完成）。"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(template: str, **kwargs) -> str:
    """将 {{name}} 占位符替换为 kwargs 值；缺失的占位符替换为空串。"""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        value = kwargs.get(key, "")
        return str(value) if value is not None else ""

    return _PLACEHOLDER.sub(_sub, template)


def render_messages(system_prompt: str, user_prompt: str) -> list[dict]:
    """组装 chat 消息。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
