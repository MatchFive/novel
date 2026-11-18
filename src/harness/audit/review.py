# 来源：语义移植自 web_dream（d:/project/web_dream/backend/app/services/check_service.py
# :110-139 调用/解析/去重；模板逐字移植 prompts.json task.consistency_scan，底本
# seed-ref.consistency_scan.md）。判据不改，逐项偏离见 task-2-report.md。
"""审计 LLM 复核段（P5 Task 2）：task.consistency_audit 单次调用 → 解析 → quote 代码层
回验 → AuditFinding。

主链（seed check_service.py:110-139 的文件化）：

1. 渲染 ``task.consistency_audit``（六变量同名传入）→ complete_structured
   （WRITING_ROUTING 路由 standard/0.2/4096；扩额重试阶梯归 tools/base，
   **LLM 调用在 store 锁外**）；
2. 解析 JSON 数组或 ``{"issues": [...]}`` 包装（seed :119 口径）；每条 item 过
   两道代码层护栏：
   - **quote 回验**（seed 只做 as_text 规整、不校验 quote 是否真在正文——本地版
     加回验）：quote 必须 locate_quote 命中 draft_text，命中不了丢弃——prompt 层
     「举证门槛：定位不到原文就不报」的代码层兜底，编造的 quote 不得进候选；
   - **字段校验**：quote 缺失 / issue 非 str / type 缺失 / 非 dict 条目一律跳过
     不炸（as_text 规整防模型给数组写崩的事故口径同 seed）；
3. item.chapter 以传入 chapter_seq 覆盖（不信模型自报）；同批内 (type, 去空白
   issue) 去重（seed :125-139 口径）。

finding 字段：rule=f"consistency_{type}"、title=issue 前 40 字、
detail=f"{type}：{issue}；建议：{suggestion}"、quote=item.quote。

失败语义（R1 裁定，不 fail-soft——T3 编排要区分「本章真无问题」与「复核失败」，
fail-soft 会让审计静默失败、用户以为扫过了）：

- **LLM 调用失败**（complete_structured reason ∈ truncated/empty/api:{kind}）→ raise
  LLMError（kind 按 client 词表映射回：truncated→starved、api:{k}→{k}，transient=True；
  complete_structured 把 LLMError 吞成 reason，这里映射回异常形态——消息带
  reason_label 映射的中文上下文，禁瞬时词）；
- **解析失败**（响应两轮后仍非 JSON = bad_json；或顶层既非 JSON 数组也非
  ``{"issues": [...]}`` 包装）→ raise ValueError（同款中文上下文）；
- complete_structured 的非 LLMError 异常（参数类等）原样上抛，不 catch；
- 唯一返回空 findings 的成功路径：调用成功且解析出空数组（模板「宁缺毋滥」的合法
  产出）——返回 ([], usage)。
"""
from __future__ import annotations

import logging

from harness.audit.rules import AuditFinding
from harness.llm.client import LLMError, OpenAICompatClient
from harness.prompts.loader import PromptLoader
from harness.reasons import reason_label
from harness.text import as_text, locate_quote
from harness.writing.pure import WRITING_ROUTING

__all__ = ["review_chapter"]

log = logging.getLogger("audit.review")

# finding.title 截断长度（seed 落库 issue 全文、界面截显；本地候选式口径取前 40 字）
_TITLE_MAX = 40


def _extract_value(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 值（数组或 {"issues": [...]} 包装，
    结构校验交给解析层）。

    extract_json 惰性导入（同 memory.extract._extract_obj 的惰性手法：tools 包
    顶层 import 链长，audit 顶层再 import tools.base 易随编排接入成环）。
    """
    from harness.tools.base import extract_json
    return extract_json(text)


# complete_structured reason → LLMError.kind（client 词表：api/timeout/starved/empty）
_KIND_BY_REASON = {"truncated": "starved", "empty": "empty"}


def _llm_error(reason: str, chapter_seq: int) -> LLMError:
    """complete_structured 的 LLM 调用失败 reason → LLMError（中文上下文，禁瞬时词）。

    complete_structured 把 LLMError 吞成 reason 返回（重试阶梯归它），这里映射回
    异常形态供 T3 分类观测：truncated/empty → starved/empty，api:{k} → {k}。
    """
    kind = (reason.split(":", 1)[1] if reason.startswith("api:")
            else _KIND_BY_REASON.get(reason, "api"))
    return LLMError(f"一致性复核失败（{reason_label(reason)}）：第{chapter_seq}章",
                    transient=True, kind=kind)


def _parse_items(data: object, *, chapter_seq: int) -> list:
    """JSON 数组或 {"issues": [...]} 包装 → 条目列表（seed :119 口径）。

    其余顶层形态（标量 / 无 issues 键 / issues 非 list）→ ValueError（R1：解析失败
    必须 raise，不得静默当「无问题」）。
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return data["issues"]
    raise ValueError(
        f"一致性复核解析失败（{reason_label('bad_json')}）：第{chapter_seq}章"
        "——顶层既非 JSON 数组也非 {\"issues\": [...]} 包装")


def _to_finding(item: object, *, chapter_seq: int,
                draft_text: str) -> tuple[AuditFinding, tuple[str, str]] | None:
    """单条目 → (AuditFinding, 去重键)；quote 回验不过 / 字段非法 → None（跳过不炸）。

    去重键 = (type, 去空白 issue)（seed :125-139 口径）——取**全文** issue 而非
    截断后的 title，防两条仅 40 字后不同的 issue 被误并。
    """
    if not isinstance(item, dict):
        return None
    quote = as_text(item.get("quote"))
    if not quote or locate_quote(draft_text, quote) is None:
        return None
    issue = item.get("issue")
    if not isinstance(issue, str) or not issue.strip():
        return None
    type_ = (as_text(item.get("type")) or "").strip()
    if not type_:
        return None
    suggestion = as_text(item.get("suggestion")) or ""
    finding = AuditFinding(
        rule=f"consistency_{type_}",
        chapter=chapter_seq,
        title=issue[:_TITLE_MAX],
        detail=f"{type_}：{issue}；建议：{suggestion}",
        quote=quote,
    )
    return finding, (type_, "".join(issue.split()))


async def review_chapter(*, llm: OpenAICompatClient, prompts: PromptLoader,
                         chapter_seq: int, draft_text: str, canonical_entries: str,
                         cast_cards: str, character_memories: str,
                         knowledge_state: str, open_promises: str,
                         ) -> tuple[list[AuditFinding], object | None]:
    """复核一章正文的一致性，返回 (findings, usage)（usage 为 LLM 响应 usage，
    未带则 None；失败 raise——LLM 调用失败 LLMError / 解析失败 ValueError，见模块
    docstring「失败语义」节）。

    LLM 调用在 store 锁外；findings 为候选式产出——落 issues 后由人工终裁。
    """
    prompt = prompts.render("task.consistency_audit",
                            canonical_entries=canonical_entries,
                            cast_cards=cast_cards,
                            character_memories=character_memories,
                            knowledge_state=knowledge_state,
                            open_promises=open_promises,
                            draft_text=draft_text)
    provider = llm.provider
    tier, temp, mt = WRITING_ROUTING["task.consistency_audit"]
    model = getattr(provider, tier, None) or provider.standard

    # 惰性 import 同 _extract_value：顶层 import 易与 tools 包成环
    from harness.tools.base import complete_structured
    data, usage, reason = await complete_structured(
        llm, model=model, temperature=temp, max_tokens=mt,
        prompt=prompt, extract=_extract_value)
    if data is None:
        if reason == "bad_json":
            raise ValueError(
                f"一致性复核解析失败（{reason_label(reason)}）：第{chapter_seq}章"
                "——响应两轮后仍非 JSON")
        raise _llm_error(reason, chapter_seq)

    findings: list[AuditFinding] = []
    seen: set[tuple[str, str]] = set()
    items = _parse_items(data, chapter_seq=chapter_seq)
    for item in items:
        parsed = _to_finding(item, chapter_seq=chapter_seq, draft_text=draft_text)
        if parsed is None:
            continue
        finding, key = parsed
        if key in seen:
            continue
        seen.add(key)
        findings.append(finding)
    log.info("一致性复核：第%s章 → 候选 %d 条（模型条目 %d）", chapter_seq,
             len(findings), len(items))
    return findings, usage
