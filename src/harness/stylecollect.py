"""文风采集流水线（P39，C3 收官项；设计 §2.6）：样本 → 分片 → 逐片标注 → 归纳档案。

seed ``web_dream/backend/app/services/style_service.py`` 的本地兑现（``split_chunks``
逐字移植；标注/归纳提示词逐字搬自 seed prompts.json，路由头钉死）。与 seed 的差异
只在持久化：seed 落库（StyleFragment/StyleProfile 表），本地落文件——
片段 ``.harness/style_fragments.json``、档案直接写成**项目 style skill**
``.harness/skills/style/SKILL.md``（写作注入走 collect_context 现成的
skills 扫描，零新挂点）。

口径（brief 钉死）：
- ``split_chunks``：段落优先（目标 1800 字/片），超长段落按句末标点再切，
  总长 > 40,000 字在句边界（≥90% 处）截断不留半句；空文本 → []。
- 逐片 ``task.style_chunk_analyze``（lite/0.2/512）：keep=false **或**
  note 空的片不入库（seed 同款防污染）。
- ``task.style_profile``（standard/0.3/2048）按**作用域内全部片段**重归纳；
  失败抛 :class:`StyleCollectError`（REPL 如实报一行）。
- append 片段续 seq / replace 清零；档案渲染沿用 seed ``_format_profile`` 格式。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.llm.client import OpenAICompatClient
    from harness.prompts.loader import PromptLoader
    from harness.store.project import ProjectStore

__all__ = [
    "StyleCollectError",
    "analyze_chunks",
    "build_profile",
    "collect",
    "read_fragments",
    "render_style_skill",
    "split_chunks",
    "write_fragments",
]

log = logging.getLogger(__name__)

STYLE_MAX_CHARS = 40_000      # 采集上限（超出按段落/句子边界截断）
CHUNK_TARGET = 1800           # 单片目标字数
SAMPLE_HEAD = 300             # 归纳档案时每个片段喂给模型的开头字数
MAX_SUMMARY_ITEMS = 40        # 逐片标注汇总最多带这么多条

# 路由（builtin 提示词首行头同款值，模块内单点钉死）：
# task.style_chunk_analyze / task.style_profile 的 tier/temperature/max_tokens。
_CHUNK_ROUTING = ("lite", 0.2, 512)
_PROFILE_ROUTING = ("standard", 0.3, 2048)

FRAGMENTS_PATH = Path(".harness") / "style_fragments.json"
STYLE_SKILL_PATH = Path(".harness") / "skills" / "style" / "SKILL.md"

# 句末标点（含其后的收尾引号）
_SENT_END = re.compile(r"(?<=[。！？!?…])[\"'”’」』]?")
_PARA_SPLIT = re.compile(r"\n\s*\n")


class StyleCollectError(Exception):
    """采集流水线失败（空样本/全片被过滤/归纳失败）——REPL 如实报一行。"""


def split_chunks(text: str, *, target: int = CHUNK_TARGET,
                 max_chars: int = STYLE_MAX_CHARS) -> list[str]:
    """把长文切成 ~target 字的片段：段落优先，超长段落（或整篇无空行）按句子边界再切。

    总长超过 max_chars 时在句子边界处截断，不留半句。seed ``split_chunks`` 逐字移植。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) > max_chars:
        head = text[:max_chars]
        marks = [m.end() for m in _SENT_END.finditer(head)]
        cut = next((pos for pos in reversed(marks) if pos >= max_chars * 0.9), None)
        text = head[:cut] if cut else head

    pieces: list[str] = []
    for para in (p.strip() for p in _PARA_SPLIT.split(text) if p.strip()):
        if len(para) <= target:
            pieces.append(para)
            continue
        buf = ""
        for sent in (s for s in _SENT_END.split(para) if s and s.strip()):
            if buf and len(buf) + len(sent) > target:
                pieces.append(buf)
                buf = sent
            else:
                buf += sent
        if buf:
            pieces.append(buf)

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 1 > target:
            chunks.append(buf)
            buf = piece
        else:
            buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks


# ------------------------------------------------------------------ 片段落盘


def read_fragments(root: Path) -> list[dict]:
    """读 ``.harness/style_fragments.json``；缺档/坏档按空列表（采集可冷启动）。"""
    try:
        data = json.loads((Path(root) / FRAGMENTS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def write_fragments(root: Path, fragments: list[dict]) -> None:
    path = Path(root) / FRAGMENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fragments, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ LLM 步骤


def _tier_model(llm: OpenAICompatClient, tier: str) -> str:
    return getattr(llm.provider, tier, None) or llm.provider.standard


async def analyze_chunks(llm: OpenAICompatClient, prompts: PromptLoader,
                         chunks: list[str]) -> list[dict]:
    """逐片 ``task.style_chunk_analyze``（lite/0.2/512）。

    单片失败当 keep=False 不炸（seed 同款 ``data or {}`` 语义）；
    keep 判定 = keep 字段非 False 且 note 非空（没解析出 note 的片不入库）。
    """
    from harness.tools.base import extract_json

    tier, temperature, max_tokens = _CHUNK_ROUTING
    analyzed: list[dict] = []
    for chunk in chunks:
        try:
            completion = await llm.complete(
                [{"role": "user",
                  "content": prompts.render("task.style_chunk_analyze", chunk=chunk)}],
                model=_tier_model(llm, tier), temperature=temperature,
                max_tokens=max_tokens)
            data = extract_json(completion.text or "")
        except Exception as exc:  # noqa: BLE001 - 单片失败不拖垮全批（seed 同款）
            log.warning("style_chunk_analyze 失败（该片丢弃）：%s", exc)
            data = None
        item = data if isinstance(data, dict) else {}
        analyzed.append({
            "text": chunk,
            "category": str(item.get("category") or "叙事")[:20],
            "note": str(item.get("note") or "")[:200],
            # 没解析出 note 的片（模型抽风）不入库，避免污染档案
            "keep": item.get("keep", True) is not False and bool(item.get("note")),
        })
    return analyzed


async def build_profile(llm: OpenAICompatClient, prompts: PromptLoader,
                        fragments: list[dict]) -> dict:
    """按**作用域内全部片段**归纳档案（``task.style_profile``，standard/0.3/2048）。

    vars：summaries（≤40 条 ``{i}. [{cat}] {note}``）、samples（每类头 300 字）、
    chunk_count、source_chars。LLM 失败/非 JSON 对象 → :class:`StyleCollectError`。
    """
    from harness.tools.base import extract_json

    if not fragments:
        raise StyleCollectError("没有可归纳的样本片段")
    summaries = "\n".join(
        f"{i}. [{f.get('category')}] {f.get('note')}"
        for i, f in enumerate(fragments[:MAX_SUMMARY_ITEMS], 1))
    by_cat: dict[str, dict] = {}
    for f in fragments:
        by_cat.setdefault(f.get("category") or "叙事", f)
    samples = "\n".join(f"【{cat}】{str(f.get('text') or '')[:SAMPLE_HEAD]}"
                        for cat, f in by_cat.items())
    source_chars = sum(int(f.get("chars") or 0) for f in fragments)

    tier, temperature, max_tokens = _PROFILE_ROUTING
    prompt = prompts.render("task.style_profile", summaries=summaries, samples=samples,
                            chunk_count=str(len(fragments)),
                            source_chars=str(source_chars))
    try:
        completion = await llm.complete(
            [{"role": "user", "content": prompt}],
            model=_tier_model(llm, tier), temperature=temperature,
            max_tokens=max_tokens)
        data = extract_json(completion.text or "")
    except Exception as exc:
        raise StyleCollectError(
            f"文风档案归纳失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise StyleCollectError("文风档案归纳失败：输出不是 JSON 对象")
    return {
        "narrative": str(data.get("narrative") or "")[:4000],
        "dialogue": str(data.get("dialogue") or "")[:4000],
        "signature_moves": [str(m)[:60] for m in (data.get("signature_moves") or [])][:10],
        "avoid": [str(a)[:60] for a in (data.get("avoid") or [])][:10],
        "source_chars": source_chars,      # 全量样本字数（追加采集会累加）
        "chunk_count": len(fragments),     # 全量片段数
    }


# ------------------------------------------------------------------ 档案渲染


def render_style_skill(profile: dict, *, source_chars: int) -> str:
    """档案 dict → 项目 style skill 的 SKILL.md 全文（seed ``_format_profile`` 格式）。

    注入侧（collect_context → skills 扫描器）按 frontmatter ``name: style`` 消费，
    正文即【文风档案…】块 + 「只模仿笔法不照搬情节」收尾句。
    """
    narrative = str(profile.get("narrative") or "").strip()
    dialogue = str(profile.get("dialogue") or "").strip()
    moves = [str(m).strip() for m in (profile.get("signature_moves") or []) if str(m).strip()]
    avoid = [str(a).strip() for a in (profile.get("avoid") or []) if str(a).strip()]
    lines = [f"【文风档案（取自 {source_chars:,} 字样本，务必贯彻）】"]
    if narrative:
        lines.append(f"叙事：{narrative}")
    if dialogue:
        lines.append(f"对话：{dialogue}")
    if moves:
        lines.append("标志手法：" + "；".join(f"{i}. {m}" for i, m in enumerate(moves, 1)))
    if avoid:
        lines.append("避用：" + "；".join(avoid))
    lines.append("（只模仿这份档案描述的笔法与语言质感，不要照搬样本的情节、人物与设定）")
    body = "\n".join(lines)
    return ("---\n"
            "name: style\n"
            "description: 项目级文风档案（/stylecollect 采集生成，写作时自动注入）\n"
            "---\n\n"
            "# 文风\n\n" + body + "\n")


# ------------------------------------------------------------------ 主编排


async def collect(*, store: ProjectStore, llm: OpenAICompatClient,
                  prompts: PromptLoader, text: str, replace: bool = False) -> dict:
    """采集主流程：分片 → 逐片标注 → 过滤入库 → 全量重归纳 → 写项目 style skill。

    ``replace=False`` 片段并入已有（seq 接着排）；``replace=True`` 清零覆盖。
    返回本次摘要 dict（chunks/kept/appended/total_chunks/source_chars）。
    """
    chunks = split_chunks(text)
    if not chunks:
        raise StyleCollectError("样本内容为空")

    analyzed = await analyze_chunks(llm, prompts, chunks)
    kept = [a for a in analyzed if a["keep"]]
    if not kept:
        raise StyleCollectError("样本中没有识别出可作样本的正文片段")

    merged: list[dict] = [] if replace else read_fragments(store.root)
    base_seq = max((int(f.get("seq") or 0) for f in merged), default=0)
    for offset, a in enumerate(kept, 1):
        merged.append({"seq": base_seq + offset, "category": a["category"],
                       "note": a["note"], "text": a["text"], "chars": len(a["text"])})
    write_fragments(store.root, merged)

    profile = await build_profile(llm, prompts, merged)
    skill_path = Path(store.root) / STYLE_SKILL_PATH
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        render_style_skill(profile, source_chars=profile["source_chars"]),
        encoding="utf-8", newline="\n")
    log.info("文风采集完成：本次 %d 片（保留 %d，%s），库内共 %d 片/%d 字",
             len(chunks), len(kept), "覆盖" if replace else "追加",
             profile["chunk_count"], profile["source_chars"])
    return {"chunks": len(chunks), "kept": len(kept), "appended": not replace,
            "total_chunks": profile["chunk_count"],
            "source_chars": profile["source_chars"]}
