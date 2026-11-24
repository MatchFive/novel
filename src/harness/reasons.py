"""LLM 失败分类串 → 观测确定性标签的共享映射（裁定 R-A：_REASON_LABELS 提升共享）。

分类契约铁律：观测一律确定性词表，禁含瞬时词（constants.TRANSIENT_MARKERS ∪
agent.loop._EXTRA_TRANSIENT_MARKERS 的任何成员，含中文同义词「超时/截断/连接」等；
映射表值不得含任何成员，tests 钉死交集为空）。complete_structured/stream_collected
的 reason 词表四类全覆盖：starved 归 "truncated"、empty、api:timeout、api:api——
truncated/empty → 无产出；api:{kind} → 调用失败；bad_json 等确定性词原样透传。

本模块是词表唯一权威源：tools/writing.py（P3）与 memory/extract.py（P4）同 import，
禁止本地复制第二份（copy-propagation：两处拷贝会各自漂移）。
"""
from __future__ import annotations

__all__ = ["REASON_LABELS", "reason_label"]

REASON_LABELS = {"truncated": "无产出", "empty": "无产出",
                 # P41：章长硬顶终检失败（压缩到 MAX_REVISE 仍超 length_cap）
                 "over_length": "字数超标"}


def reason_label(reason: str) -> str:
    """LLM 分类串 → 观测用确定性类别。

    api:{kind}（timeout/api 等瞬时根因）归「调用失败」；truncated/empty 归「无产出」；
    其余确定性词（bad_json 等）原样透传；空串归「无产出」。
    """
    if not reason:
        return "无产出"
    if reason.startswith("api:"):
        return "调用失败"
    return REASON_LABELS.get(reason, reason)
