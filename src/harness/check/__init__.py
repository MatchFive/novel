# P6 验收链（设计 §1.5）：纯函数层
"""验收链的纯函数层——分层抽样、章定位、意见解析与 quote 回验。

分层（对应设计 §1.5 与后续任务的编排/工具层）：

- ``sampling``：分层抽样（ConStory-Bench《Lost in the Middle》口径）+ 章定位串解析；
- ``parser``：LLM 验收输出的解析、**quote 代码层回验**、verdict/checklist 语义；
- （T3 起）``context`` / ``runner``：上下文组装与编排落库。

本层是**纯函数**：零 I/O、零 LLM、零 store 依赖——``ChapterDoc`` 只作类型标注与
只读字段访问（``seq`` / ``meta["title"]`` / ``body``）。

纪律（本层最要紧的一条）：**凡能定位到正文的意见，必须带一段能在该章正文里
locate_quote 命中的逐字 quote**——命中不了就丢弃。模型会把动机误读、会把不存在的
句子当原文报上来（FlawedFictions 教训），quote 是唯一可核的锚。

**这条纪律按 kind 分层**（``parse_check_issues`` 的 ``require_quote``）：全书级验收
（``global_check``）章级意见**必须**带 quote；情节级验收（``check_plots``）**不要求**
（seed 的情节级输出格式没有 quote 字段——意见针对情节结构，未必有可引的原文片段），
但**给了 quote 仍要回验**（编造的照丢）。
"""
from __future__ import annotations

from harness.check.parser import (
    CheckIssue,
    parse_check_issues,
    parse_verdict,
    verdict_from_checklist,
)
from harness.check.sampling import (
    SAMPLE_CHARS_PER_CHAPTER,
    SAMPLE_MAX_CHAPTERS,
    match_chapter,
    sample_chapters,
)

__all__ = [
    "SAMPLE_CHARS_PER_CHAPTER",
    "SAMPLE_MAX_CHAPTERS",
    "CheckIssue",
    "match_chapter",
    "parse_check_issues",
    "parse_verdict",
    "sample_chapters",
    "verdict_from_checklist",
]
