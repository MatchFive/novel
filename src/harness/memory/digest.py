# 卷级前情提要 DigestKeeper（P4 Task 6，设计 §2.9）
"""卷级滚动 digest ``memory/digests/vol-<NN>.md`` 的增量维护。

纪律（设计 §2.9 原文）：digest 是**投影不是事实源**（事实源 = chapters 正文 +
memory/*），可随时整卷重建，与正文漂移时以正文为准重建后覆盖。本模块只从
MemoryStore 的章记忆（T4）读 events 投影出情节节，不持有任何独立事实。

- 形态：每卷一个 md，卷头 + 若干 ``## 情节 <seq>. <title>`` 节；节内每章一段
  （``### 第N章《title》`` + 事件行）。
- ``maintain``：情节定稿后增量维护——先清后建（该情节节已存在则整节替换，幂等重跑
  安全）；单卷摘要超 ``DIGEST_LIMIT``（2000 字）时把最早情节节（不含本次新节）交
  ``task.digest_compress``（路由 lite/0.3/2048）压缩，只压旧段不整卷重写。
- 压缩失败降级 = 只追加不压缩（宁可冗长不可丢）：LLM 失败/解析失败/产出空段都
  不 raise、不丢原文，跳过该节试下一旧节，全部失败则保持冗长。
- ``rebuild_async``：整卷先清后建（投影可随时重建）；``read`` 无则 "".
- digest 不进检索语料（T3 build_corpus 已排除 digests/，本模块保持同口径不引用检索）。
- 锁模式复用 MemoryStore 的 ``_hold``/``_write``；**LLM 调用在锁外**（先锁内读出
  基线 → 锁外压缩 → 写前重读合并并发新增节，同 store 聚合纪律）。
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from harness.llm.client import OpenAICompatClient
from harness.memory.store import MemoryStore
from harness.prompts.loader import PromptLoader
from harness.reasons import reason_label
from harness.text import as_list, as_text
from harness.writing.pure import WRITING_ROUTING

if TYPE_CHECKING:   # 惰性 import 的类型标注（tools.base 与本模块成环，见 _extract_obj）
    from harness.tools.base import UsageMeter

__all__ = ["DIGEST_LIMIT", "DigestKeeper"]

log = logging.getLogger("memory.digest")

# 单卷摘要压缩触发线（设计 §2.9：超 ~2000 字压最早情节节）。
DIGEST_LIMIT = 2000

_COMPRESSED_MARK = "（已压缩）"
_SECTION_HEAD = re.compile(r"^## 情节 (\d+)\.\s+(.*?)(（已压缩）)?\s*$")


def _extract_obj(text: str) -> object | None:
    """结构化输出的默认 extract：首个可解析 JSON 对象（结构校验交给解析层）。

    extract_json 惰性导入（tools.base 顶层 import outline、writing 顶层 import
    memory 包，本模块顶层再 import tools.base 会成环——同 memory.extract 的惰性手法）。
    """
    from harness.tools.base import extract_json
    value = extract_json(text)
    return value if isinstance(value, dict) else None


class DigestKeeper:
    """卷级 digest 读写入口。``mem`` 为项目 MemoryStore（章记忆唯一读方）。"""

    def __init__(self, mem: MemoryStore):
        self._mem = mem

    # ------------------------------------------------------------------ 路径

    def _path(self, vol: int):
        return self._mem.root / "memory" / "digests" / f"vol-{vol:02d}.md"

    def read(self, vol: int) -> str:
        """digest 全文；文件缺失/非 UTF-8 返 ""（不 raise）。"""
        path = self._path(vol)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ""

    # -------------------------------------------------------------- 节解析/渲染

    @staticmethod
    def _parse(text: str) -> list[dict]:
        """digest 文本 → 节列表 [{seq, title, compressed, body}]（按文件序）。"""
        sections: list[dict] = []
        cur: dict | None = None
        for line in (text or "").split("\n"):
            m = _SECTION_HEAD.match(line.strip())
            if m:
                cur = {"seq": int(m.group(1)), "title": m.group(2).strip(),
                       "compressed": m.group(3) == _COMPRESSED_MARK, "body": []}
                sections.append(cur)
            elif line.startswith("# "):
                continue    # 卷头
            elif cur is not None and line.strip():
                cur["body"].append(line.rstrip())
        for s in sections:
            s["body"] = "\n".join(s["body"]).strip()
        return sections

    @staticmethod
    def _render(vol: int, sections: list[dict]) -> str:
        parts = [f"# 第{vol}卷前情提要", ""]
        for s in sections:
            mark = _COMPRESSED_MARK if s["compressed"] else ""
            parts.append(f"## 情节 {s['seq']}. {s['title']}{mark}")
            parts.append("")
            if s["body"]:
                parts.append(s["body"])
                parts.append("")
        return "\n".join(parts).rstrip("\n") + "\n"

    # -------------------------------------------------------------- 情节节构建

    def _build_plot_section(self, chapter_seqs: list[int]) -> str:
        """本情节段落：每章一段（``### 第N章《title》`` + 事件行）。

        事件行取章记忆 events 的 title/summary，附非空地点/时间标注；章记忆缺失
        （提取失败/被清）整章跳过——digest 是投影，事实源仍在 memory/*。
        """
        blocks = []
        for seq in chapter_seqs or []:
            data = self._mem.read_chapter_memory(seq)
            if data is None:
                log.warning("digest 维护：第%s章记忆缺失，整章跳过", seq)
                continue
            lines = [f"### 第{seq}章《{as_text(data.get('title')) or ''}》"]
            for ev in data.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                line = f"- {as_text(ev.get('title')) or ''}——{as_text(ev.get('summary')) or ''}"
                segs = [as_text(ev.get("location")) or "",
                        as_text(ev.get("time_marker")) or ""]
                segs = [s for s in segs if s]
                if segs:
                    line += f"（{'；'.join(segs)}）"
                lines.append(line)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ 压缩

    async def _compress_section(self, *, llm: OpenAICompatClient,
                                prompts: PromptLoader, settings, vol: int,
                                section: dict,
                                meter: UsageMeter | None = None) -> str | None:
        """单节压缩：task.digest_compress → 压缩后段落文本；失败返 None（降级口）。

        失败含：无 provider / LLM 调用失败 / 解析失败 / 产出空段——一律不 raise，
        由调用方决定跳过或保持冗长。
        ``meter`` 可选（P5 usage 入账）：压缩成功时把该次调用的 usage 记进计量器
        （压缩失败不记 usage、降级口径不变）。
        """
        provider = settings.providers.get(settings.default_provider) if settings else None
        if provider is None:
            log.warning("digest 压缩失败（调用失败）：无 provider"
                        "（default_provider=%r）",
                        getattr(settings, "default_provider", None))
            return None
        tier, temp, mt = WRITING_ROUTING["task.digest_compress"]
        model = getattr(provider, tier, None) or provider.standard
        prompt = prompts.render("task.digest_compress",
                                segments=section["body"], vol=str(vol))
        # 惰性 import 同 _extract_obj：顶层 import 会与 tools 包成环
        from harness.tools.base import complete_structured
        data, usage, reason = await complete_structured(
            llm, model=model, temperature=temp, max_tokens=mt,
            prompt=prompt, extract=_extract_obj)
        if data is None:
            log.warning("digest 压缩失败（%s）：第%s卷情节节 %s",
                        reason_label(reason), vol, section["seq"])
            return None
        if meter is not None:
            meter.add(usage)   # usage 入账（压缩成功才记）
        segs = [as_text(x) for x in as_list(data.get("segments"))]
        segs = [s for s in segs if s]
        if not segs:
            log.warning("digest 压缩产出空段：第%s卷情节节 %s——保持原文", vol,
                        section["seq"])
            return None
        return "\n\n".join(segs)

    async def _enforce_limit(self, *, llm, prompts, settings, vol: int,
                             text: str, keep_seq: int | None,
                             meter: UsageMeter | None = None) -> str:
        """超 DIGEST_LIMIT 时从最早节起压缩旧段（不含 keep_seq 新节）。

        压缩失败跳过该节试下一旧节；无可压节或全部失败则保持冗长（宁可冗长不可丢）。
        ``meter`` 可选（P5 usage 入账）：逐层下传给压缩调用。
        """
        failed: set[int] = set()
        while len(text) > DIGEST_LIMIT:
            sections = self._parse(text)
            target = next((s for s in sections
                           if s["seq"] != keep_seq and not s["compressed"]
                           and s["seq"] not in failed and s["body"]), None)
            if target is None:
                log.warning("第%s卷 digest 超 %d 字但无旧节可压缩——保持冗长",
                            vol, DIGEST_LIMIT)
                break
            new_body = await self._compress_section(
                llm=llm, prompts=prompts, settings=settings, vol=vol,
                section=target, meter=meter)
            if new_body is None:
                failed.add(target["seq"])
                continue
            target["compressed"] = True
            target["body"] = new_body
            text = self._render(vol, sections)
        return text

    # ------------------------------------------------------------------ 维护

    async def maintain(self, *, llm: OpenAICompatClient, prompts: PromptLoader,
                       settings, vol: int, plot_seq: int, plot_title: str,
                       chapter_seqs: list[int],
                       meter: UsageMeter | None = None) -> None:
        """情节定稿后增量维护（见模块 docstring）：先清后建 + 超限压旧段。

        LLM 压缩在锁外；压缩失败只追加不压缩（不 raise）。幂等：同情节重跑整节
        替换，文件无重复节。
        ``meter`` 可选（P5 usage 入账）：压缩调用的 usage 记进计量器；None=不记账，
        行为与从前逐字相同。
        """
        path = self._path(vol)
        body = self._build_plot_section(chapter_seqs)
        with self._mem._hold(path):
            sections = [s for s in self._parse(self.read(vol))
                        if s["seq"] != plot_seq]
            sections.append({"seq": plot_seq, "title": plot_title,
                             "compressed": False, "body": body})
            sections.sort(key=lambda s: s["seq"])
            text = self._render(vol, sections)
        text = await self._enforce_limit(llm=llm, prompts=prompts, settings=settings,
                                         vol=vol, text=text, keep_seq=plot_seq,
                                         meter=meter)
        with self._mem._hold(path):
            # 写前重读合并：锁外压缩期间并发新增的其他情节节不丢（同 store 聚合纪律）
            mine = {s["seq"]: s for s in self._parse(text)}
            current = {s["seq"]: s for s in self._parse(self.read(vol))}
            merged = sorted({**current, **mine}.values(), key=lambda s: s["seq"])
            self._mem._write(path, self._render(vol, merged))

    async def rebuild_async(self, *, llm: OpenAICompatClient,
                            prompts: PromptLoader, settings, vol: int,
                            plots: list[dict]) -> None:
        """整卷先清后建（投影纪律：可随时整卷重建）。

        ``plots`` = [{"seq", "title", "chapter_seqs"}]；整卷重建后同样守 2000 字
        压缩纪律（keep_seq 取最新情节节，只压旧段）。
        """
        path = self._path(vol)
        sections = []
        for plot in plots or []:
            if not isinstance(plot, dict):
                continue
            try:
                seq = int(plot.get("seq"))
            except (TypeError, ValueError):
                continue
            sections.append({
                "seq": seq,
                "title": (as_text(plot.get("title")) or f"情节{seq}").strip(),
                "compressed": False,
                "body": self._build_plot_section(
                    [s for s in as_list(plot.get("chapter_seqs"))
                     if isinstance(s, int)]),
            })
        sections.sort(key=lambda s: s["seq"])
        text = self._render(vol, sections)
        keep = sections[-1]["seq"] if sections else None
        text = await self._enforce_limit(llm=llm, prompts=prompts, settings=settings,
                                         vol=vol, text=text, keep_seq=keep)
        with self._mem._hold(path):
            self._mem._write(path, text)
