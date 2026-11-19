"""轻量修改路径：edit_by_command（§2.1）。

LLM（task.auto_modify_propose，standard 档）拿到 instruction + 可编辑文件现状，产出
[{path, content}] 全文件新内容；harness 用 difflib 渲染 unified diff 预览 → ApprovalUI.ask
逐文件多选确认——**确认前零写盘**：ask 返回前任何路径不得触碰 md 文件 → 通过的文件经 store
写盘（world.md 按标题合并 / 角色卡整卡替换 / arcs 按 vol 合并 / plots 按 seq 合并；
合并语义意味着 LLM 未返回的既有条目保留，防误删）。

范围只四类：world.md / characters/*.md / outline/arcs.md / outline/plots/vol-*.md；
chapters 拒绝并说明 P3 支持。任一文件 front-matter 解析失败 → 该文件跳过 + 观测说明。
重试链与 plan_arcs 同口径（Task 8 起同底座）：空响应/解析失败扩额一次，失败观测分类
truncated/bad_json/empty——阶梯单点化在 base.complete_structured，观测文本/重试次数/
扩额公式不变，返回值包 ToolResult（usage 入账）。
"""
from __future__ import annotations

import difflib
import logging
import re

import yaml

from harness.llm.client import OpenAICompatClient
from harness.llm.policy import resolve_max_tokens
from harness.memory.store import MemoryStore
from harness.projects import slugify
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, ApprovalUI, ask_ui_async
from harness.skills import skill_injection
from harness.store.frontmatter import split_document
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.base import ToolResult, _run_sync, complete_structured
from harness.tools.outline import _extract_json_value, _LastErrorProbe, _normalize_arcs
from harness.tools.registry import ToolRegistry
from harness.writing.pure import WRITING_ROUTING

__all__ = ["edit_by_command", "register_modify_tools", "resequence_chapters"]

log = logging.getLogger("tools.modify")

_DIFF_MAX_LINES = 60  # 单个文件 diff 预览上限，超出截断注记（终端卡片可读性）

_VOL_PLOT = re.compile(r"^vol-(\d+)\.md$")
_PLOT_REF = re.compile(r"^(\d+)-(\d+)$")
# skill 目录名（P36，C2「skill 的 LLM 自动创建」）：与 slugify 同字符类——无 /、无 ..、
# 无盘符可越界；落点名只信 path 段，front-matter 的 name: 只是 metadata 不参与路径
_SKILL_NAME = re.compile(r"^[\w一-鿿-]+$")
# 章记忆文件头（「# 第N章记忆《title》」）：按标题定位记忆实际章号（transit 自愈用）
_MEM_HEAD = re.compile(r"^# 第\d+章记忆《(.*?)》")


class _Proposal:
    """一个范围内修改提议：path（规范相对路径）+ content（全文件新内容）+ kind（store 写盘口径）。"""

    def __init__(self, path: str, content: str, kind: str, vol: int | None = None):
        self.path = path
        self.content = content
        self.kind = kind
        self.vol = vol


def _classify_path(raw: str) -> tuple[str | None, str | None, int | None]:
    """路径规范化 + 范围判定 → (kind, 规范路径, vol) 或 (None, 拒绝原因, None)。

    只认项目内相对路径（../ 与盘符绝对路径一律拒绝）；反斜杠归一为 '/'。
    """
    norm = raw.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    parts = [seg for seg in norm.split("/") if seg and seg != "."]
    if not parts or ".." in parts or norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return None, "路径越界或非项目内相对路径", None
    rel = "/".join(parts)
    if parts == ["world.md"]:
        return "world", rel, None
    if parts[0] == "characters" and len(parts) == 2 and parts[1].endswith(".md"):
        return "character", rel, None
    if parts == ["outline", "arcs.md"]:
        return "arcs", rel, None
    if len(parts) == 3 and parts[0] == "outline" and parts[1] == "plots":
        m = _VOL_PLOT.match(parts[2])
        if m:
            return "plots", rel, int(m.group(1))
    # P36：项目级 skill 的 LLM 自动创建/改写走既有轻量修改路径（设计 §2.6）——
    # 复用 edit_by_command 确认门，不新增并发写入口
    if len(parts) == 4 and parts[0] == ".harness" and parts[1] == "skills":
        if parts[3] == "SKILL.md" and _SKILL_NAME.match(parts[2]):
            return "skill", rel, None
        return None, ("skill 名非法（仅字母/数字/CJK/下划线/连字符）"
                      if parts[3] == "SKILL.md"
                      else "skill 路径必须是 .harness/skills/<name>/SKILL.md"), None
    if parts[0] == "chapters":
        return None, "章节正文不支持经本工具修改（P3 支持）", None
    return None, ("不在可修改范围（仅 world.md / characters/*.md / outline/arcs.md / "
                  "outline/plots/vol-*.md / .harness/skills/<name>/SKILL.md）"), None


def _snapshot(store: ProjectStore) -> dict[str, str]:
    """可编辑文件现状（只读）：world.md + characters/*.md + outline/arcs.md + outline/plots/vol-*.md。"""
    root = store.root
    files: dict[str, str] = {}

    def _add(rel: str, path) -> None:
        if path.is_file():
            files[rel] = path.read_text(encoding="utf-8")

    _add("world.md", root / "world.md")
    characters = root / "characters"
    if characters.is_dir():
        for p in sorted(characters.iterdir()):
            if p.is_file() and p.name.endswith(".md"):
                _add(f"characters/{p.name}", p)
    _add("outline/arcs.md", root / "outline" / "arcs.md")
    plots = root / "outline" / "plots"
    if plots.is_dir():
        for p in sorted(plots.iterdir()):
            if p.is_file() and _VOL_PLOT.match(p.name):
                _add(f"outline/plots/{p.name}", p)
    # P36：项目级 skill 也在【现状】里（模型可见才能扩建/改写，不覆盖乱写）
    skills = root / ".harness" / "skills"
    if skills.is_dir():
        for d in sorted(skills.iterdir()):
            f = d / "SKILL.md"
            if d.is_dir() and f.is_file():
                _add(f".harness/skills/{d.name}/SKILL.md", f)
    return files


def _render_snapshot(files: dict[str, str]) -> str:
    return "\n\n".join(f"===== {rel} =====\n{text}" for rel, text in files.items()) or "（无可编辑文件）"


def _parse_edits(text: str) -> list[tuple[str, str]] | None:
    """解析 [{path, content}]；接受 {"edits"/"files": [...]} 包装。无有效条目返回 None（bad_json）。"""
    value = _extract_json_value(text)
    if isinstance(value, dict):
        value = value.get("edits") if isinstance(value.get("edits"), list) else value.get("files")
    if not isinstance(value, list):
        return None
    edits = [(item["path"], item["content"]) for item in value
             if isinstance(item, dict)
             and isinstance(item.get("path"), str) and isinstance(item.get("content"), str)]
    return edits or None


def _unified_diff(path: str, old: str, new: str) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                      fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
    if len(lines) > _DIFF_MAX_LINES:
        lines = lines[:_DIFF_MAX_LINES] + [f"…（diff 截断，共 {len(lines)} 行）"]
    return "\n".join(lines)


def _apply(store: ProjectStore, prop: _Proposal) -> str | None:
    """经 store 写盘一个通过确认的文件；返回失败原因（跳过注记），成功返回 None。

    front-matter 解析失败 → 返回注记（该文件跳过，绝不上覆盖坏档）。store 私有静态解析
    （_parse_world/_parse_plots 一族）为纯函数复用——与同包内复用同口径，P4 可收公共 helper。
    """
    try:
        if prop.kind == "character":
            meta, _body = split_document(prop.content)
            meta.setdefault("name", prop.path.rsplit("/", 1)[-1][:-3])  # 缺 name 用文件名兜底
            store.upsert_character(meta)
        elif prop.kind == "arcs":
            meta, _body = split_document(prop.content)
            arcs = meta.get("arcs")
            good, dropped = _normalize_arcs(arcs if isinstance(arcs, list) else [])
            if not good:
                return f"front-matter 无有效 arcs 列表（丢弃 {dropped} 条）"
            store.upsert_arcs(good)
        elif prop.kind == "world":
            _prefix, blocks = ProjectStore._parse_world(prop.content)
            entries = [ProjectStore._parse_world_entry(cat, title, raw)
                       for cat, title, raw in (b[1:] for b in blocks if b[0] == "entry")]
            if not entries:
                return "解析不到世界观条目"
            store.upsert_world_entries(entries)
        elif prop.kind == "plots":
            _prefix, blocks = ProjectStore._parse_plots(prop.content)
            plots = [ProjectStore._parse_plot_block(seq, title, raw) for seq, title, raw in blocks]
            if not plots:
                return "解析不到情节条目"
            store.upsert_plots(prop.vol, plots)
        elif prop.kind == "skill":
            # P36：SKILL.md 自由格式 md（skills.py 读取方容忍坏档=跳过），无需 front-matter 校验
            name = prop.path.split("/")[2]
            path = store.root / ".harness" / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prop.content, encoding="utf-8", newline="\n")
    except (yaml.YAMLError, ValueError) as exc:
        return f"front-matter 解析失败：{exc}"
    return None


# ------------------------------------------------------------------ 现实参照预检（P5 T10）

#: 触发词（instruction 指向世界观才预检——**刻意窄**：误触发只多花 LLM 调用与噪音，
#: 漏触发只是少一次预检）
_WORLD_KEYWORDS = ("world.md", "世界观", "世界设定", "世界条目")

#: 预检取用的现实参照条数上限（seed REAL_REFS_MAX 同值）
_REAL_REFS_MAX = 6

#: 【已核实参照】块的标题（进 propose 提示词）
_REF_BLOCK_HEAD = "【已核实参照】"


def _world_targets(instruction: str) -> bool:
    """instruction 是否指向世界观（触发现实参照预检）。"""
    text = (instruction or "")
    return any(k in text for k in _WORLD_KEYWORDS)


def _parse_refs(text: str) -> list[dict]:
    """解析 ``{"refs":[{"name","query"}]}`` → [{name, query}]（坏形态 → 空列表）。"""
    try:
        from harness.tools.base import extract_json
        value = extract_json(text)
    except Exception:  # noqa: BLE001 - 解析失败按「模型没列出参照」处理
        return []
    if not isinstance(value, dict):
        return []
    refs = value.get("refs")
    if not isinstance(refs, list):
        return []
    out = []
    for item in refs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or "").strip() or name
        if name:
            out.append({"name": name, "query": query})
    return out[:_REAL_REFS_MAX]


async def _ref_check_block(*, store: ProjectStore, llm, prompts: PromptLoader,
                            settings, instruction: str, emit=None) -> str:
    """现实参照预检 → 【已核实参照】块（失败不阻断修改路径）。

    流程：LLM 渲 ``task.auto_ref_check``（需求/约束/坐标）→ 解析 refs（≤6）→ 模型列不出
    则用确定性兜底（``websearch.real_world_refs``）→ 逐条 ``verify(rounds=1)`` →
    **只回显真查到结论的** → 落 research.md + ``real_ref_checked`` 事件。

    返回 "" 表示「没跑」或「没查到可回显的结论」——调用点据此零变化拼接。
    """
    if settings is None or not getattr(settings, "web_verify", False):
        return ""
    if getattr(settings, "search", None) is None:
        return ""
    if not _world_targets(instruction):
        return ""

    constraints = ""
    try:
        # 约束槽 = 现有 world 条目标题（坏 world.md → 降级为空，**不抛**——
        # 预检不得让整条修改路径崩掉；既有 _snapshot 只读原始文本，坏档走逐文件降级）
        constraints = "\n".join(
            f"- {title}" for title in
            (str(e.get("title") or "") for e in store.read_world()) if title)
    except Exception as exc:  # noqa: BLE001 - 预检必须不阻断修改路径（含非 UTF-8 world.md）
        log.warning("现实参照预检跳过：world 条目读取失败（%s）", exc)
        return ""
    try:
        prompt = prompts.render("task.auto_ref_check", premise=instruction,
                                constraints=constraints, variation="")
        provider = llm.provider
        tier, temp, mt = WRITING_ROUTING["task.auto_ref_check"]
        model = getattr(provider, tier, None) or provider.standard
        completion = await llm.complete([{"role": "user", "content": prompt}],
                                        model=model, temperature=temp, max_tokens=mt)
        refs = _parse_refs(completion.text)
    except Exception as exc:  # noqa: BLE001 - 预检失败不阻断修改路径
        log.warning("现实参照预检失败（已跳过，不阻断修改路径）：%s", exc)
        return ""

    if not refs:
        # 模型列不出参照 → 确定性兜底（《书名》 + 常见真实作品 + 题材正典）
        from harness.websearch import real_world_refs

        refs = [{"name": r, "query": r} for r in real_world_refs(instruction)][:_REAL_REFS_MAX]
    if not refs:
        return ""

    from harness.websearch import WebSearchService, append_research

    service = WebSearchService(settings.search, llm, prompts)
    lines: list[str] = []
    try:
        for ref in refs:
            try:
                entry = await service.verify(ref["query"], kind="ref")
            except Exception as exc:  # noqa: BLE001 - 单条核实失败不阻断其余
                log.warning("现实参照核实失败（已跳过）：%s（%s）", ref["name"], exc)
                continue
            if entry is None or not entry.conclusion:
                continue    # 只回显真查到结论的
            if not entry.sources:
                # 无检索结果支撑 → 视为未核实（结论非空但没有出处 = 凭记忆编的）
                log.warning("现实参照核实跳过（无检索结果支撑）：%s", ref["name"])
                continue
            append_research(store.root, entry)
            lines.append(f"· {ref['name']}：{entry.conclusion}")
    finally:
        await service.aclose()

    if not lines:
        return ""
    if emit is not None:
        emit("real_ref_checked", {"count": len(lines)})
    return "\n\n" + "\n".join([_REF_BLOCK_HEAD, *lines]) + "\n"


async def edit_by_command(store: ProjectStore, llm: OpenAICompatClient, prompts: PromptLoader,
                          ui: ApprovalUI, args: dict, *, emit=None,
                          settings=None) -> ToolResult:
    """修改指令 → LLM 提议 → diff 确认门 → 通过的文件经 store 写盘；返回应用结果观测。

    提议生成的扩额阶梯在 base.complete_structured 单点执行（与内联版同公式同次数）；
    失败观测 detail 经 _LastErrorProbe 取回，观测文本与迁移前逐字节一致。

    **现实参照预检**（P5 T10，裁定 18）：instruction 指向世界观且联网核查启用时，
    在 propose **渲染前**跑 ``task.auto_ref_check`` → 逐条核实 → 已核实结论以
    【已核实参照】块追加进提示词。**只回显真查到结论的**；预检失败不阻断修改路径。
    """
    instruction = str(args.get("instruction") or "").strip()
    if not instruction:
        return ToolResult(text="edit_by_command 需要 args.instruction（修改指令），当前为空",
                          usage=None)
    snapshot = _snapshot(store)
    ref_block = await _ref_check_block(store=store, llm=llm, prompts=prompts,
                                       settings=settings, instruction=instruction,
                                       emit=emit)
    prompt = prompts.render("task.auto_modify_propose",
                            files=_render_snapshot(snapshot), instruction=instruction) \
        + skill_injection(store.root, "task.auto_modify_propose") \
        + ref_block
    fail_reason = ""

    def _extract(text: str):
        nonlocal fail_reason
        edits = _parse_edits(text)
        if edits is None:
            fail_reason = "响应中解析不出 [{path, content}] JSON"
            return None
        return edits

    probe = _LastErrorProbe(llm)
    value, usage, reason = await complete_structured(
        probe, model=llm.provider.standard, temperature=0.3,
        max_tokens=resolve_max_tokens(len(prompt)), prompt=prompt, extract=_extract)
    if value is None:
        if reason == "bad_json":
            return ToolResult(
                text=f"edit_by_command 失败（bad_json）：两次调用均未能产出修改提议——"
                     f"{fail_reason}", usage=usage)
        exc = probe.last
        detail = (f"finish_reason={exc.finish_reason}" if exc is not None and exc.finish_reason
                  else ((str(exc) or exc.kind) if exc is not None else reason))
        return ToolResult(text=f"edit_by_command 失败（{reason}）：{detail}", usage=usage)
    edits = value

    proposals: list[_Proposal] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw_path, content in edits:
        kind, rel, vol = _classify_path(raw_path)
        if kind is None:
            rejected.append(f"{raw_path}（{rel}）")
            continue
        if rel in seen:
            rejected.append(f"{rel}（重复提议，取首次）")
            continue
        seen.add(rel)
        proposals.append(_Proposal(rel, content, kind, vol))
    if not proposals:
        lines = ["edit_by_command：无范围内文件可应用（零变更）"]
        if rejected:
            lines.append("拒绝：" + "；".join(rejected))
        return ToolResult(text="\n".join(lines), usage=usage)

    # diff 预览（只读快照对比）→ 确认门。此处与 ask 之间没有任何写盘。
    diffs: dict[str, str] = {}
    unchanged: list[str] = []
    for prop in proposals:
        if prop.content == snapshot.get(prop.path, ""):
            unchanged.append(prop.path)
            continue
        diffs[prop.path] = _unified_diff(prop.path, snapshot.get(prop.path, ""), prop.content)
    if not diffs:
        return ToolResult(text="edit_by_command：提议内容与现状一致（零变更）", usage=usage)
    options = [f"应用 {path}" for path in diffs]
    question = (f"edit_by_command 申请 {len(diffs)} 个文件修改（空格分隔可多选）：\n{instruction}\n\n"
                + "\n\n".join(diffs.values()))
    try:
        choice = await ask_ui_async(ui, question, options, multi=True)
    except ApprovalCancelled:
        return ToolResult(text="edit_by_command：已取消，零变更（未写任何文件）", usage=usage)

    approved = set(choice.indices)
    by_path = {p.path: p for p in proposals}
    applied: list[str] = []
    notes: list[str] = []
    for idx, path in enumerate(diffs):
        if idx not in approved:
            notes.append(f"{path}（未勾选）")
            continue
        note = _apply(store, by_path[path])
        if note is None:
            applied.append(path)
        else:
            notes.append(f"{path}（{note}）")
    lines = [f"edit_by_command：已应用 {len(applied)} 个文件" + ("：" + "、".join(applied) if applied else "")]
    if applied:
        # git 自动快照（拍板⑧）：批量设定变更落盘即 commit；失败静默降级
        from harness.snapshot import auto_snapshot
        auto_snapshot(store.root, "批量设定变更",
                      f"edit_by_command {len(applied)} 个文件（{'、'.join(applied)}）")
    if unchanged:
        lines.append("无变化：" + "、".join(unchanged))
    if notes:
        lines.append("跳过：" + "；".join(notes))
    if rejected:
        lines.append("拒绝：" + "；".join(rejected))
    return ToolResult(text="\n".join(lines), usage=usage)


def register_modify_tools(reg: ToolRegistry, *, store: ProjectStore, llm: OpenAICompatClient,
                          prompts: PromptLoader, ui: ApprovalUI) -> None:
    """注册 edit_by_command（read_only=False）：写盘工具，确认门（ui）注入式，测试传假实现。"""
    def handler(store_: ProjectStore, args: dict) -> str:
        return _run_sync(edit_by_command(store_, llm, prompts, ui, args)).text

    reg.register("edit_by_command", handler, read_only=False)
    # resequence_chapters 是纯文件重编（零 LLM），handler 本身就是 (store,args)->str 签名。
    reg.register("resequence_chapters", resequence_chapters, read_only=False)


# ---------------------------------------------------------------- resequence_chapters（P4 Task 8）

def _resequence_plan(store: ProjectStore) -> tuple[list[tuple[ChapterDoc, int]], list[str]]:
    """现状章节按「卷序→情节序→章号」排序后的 (doc, 新seq) 对 + 坏章跳过注记。

    无 plot_id（或格式非法）的章排最后（保持相对章号序，不猜归属）。
    """
    notes: list[str] = []
    docs = store.list_chapters(
        on_error=lambda p, e: notes.append(f"{p.name}（坏章跳过：{e.reason}）"))

    def _key(d: ChapterDoc) -> tuple[int, int, int]:
        pid = str(d.meta.get("plot_id") or "")
        m = _PLOT_REF.match(pid)
        vol, plot = (int(m.group(1)), int(m.group(2))) if m else (1 << 30, 1 << 30)
        return (vol, plot, d.seq)

    ordered = sorted(docs, key=_key)
    return [(d, i) for i, d in enumerate(ordered, 1)], notes


def _mem_seq_by_title(root, title: str, fallback: int) -> int:
    """按「# 第N章记忆《title》」头定位章记忆文件的实际章号（找不到回退 fallback）。

    自愈关键（评审 Important-1）：记忆 rename 中途失败时文件可能停在 transit 号，
    不能假定它还在章 doc.seq——按章标题找回真实位置，重跑 resequence 即可收敛。
    """
    mem = root / "memory"
    if title and mem.is_dir():
        for p in sorted(mem.glob("*.md")):
            m = re.match(r"^(\d{4})\.md$", p.name)
            if not m:
                continue
            try:
                first = p.read_text(encoding="utf-8").split("\n", 1)[0]
            except (OSError, UnicodeDecodeError):
                continue
            hm = _MEM_HEAD.match(first)
            if hm and hm.group(1) == title:
                return int(m.group(1))
    return fallback


def resequence_chapters(store: ProjectStore, args: dict) -> str:
    """按「卷序→情节序」重排全书章节 seq 1..N（设计附录 A.4；零 LLM 同步工具）。

    两趟中转防串号（中转号 = 新号 + offset，offset = 最大现用章号，避开一切现用
    章号）：第一趟 old→中转、第二趟 中转→最终；章文件写入与记忆重命名**逐章容错**
    （评审 Important-1，照记忆 rename 失败路径风格）：单章 write_chapter 抛错 →
    该章保持原位、观测注记（章号区间+异常类型名，零瞬时词）、其余章照常重排；
    记忆 rename 抛错 → 同样不阻断。记忆按章标题定位（``_mem_seq_by_title``），
    任何中途失败残留（章/记忆停在 transit 号、甚至两记忆互换位置）都经自愈趟
    （transit 号两小趟归位）消化——同趟内消化不完的，重跑本工具即收敛。
    与目标章号同前缀的非本章文件（坏章等，不在重排范围）写前备份、写后还原，
    绝不被 write_chapter 的同名清理顺带删掉。
    只删**本章**旧号文件（slug 匹配）：失败重试趟可能与别章同前缀，glob 全删
    会误伤——同前缀非本章文件绝不碰。
    """
    pairs, notes = _resequence_plan(store)
    if not pairs:
        return "resequence_chapters：无章节，零变更"
    mem = MemoryStore(store.root)
    mem_broken: list[str] = []
    ch_broken: list[str] = []

    def _rename_mem(old: int, new: int) -> bool:
        """记忆重命名（裁定 8 风格）：失败不阻断，观测如实注记，返回是否成功。"""
        if old == new:
            return True
        try:
            mem.rename_chapter_seq(old, new)
        except Exception:
            # M-a：非阻断注记避开「失败（」——loop._classify 见「失败（」判 failed
            # 计熔断，与 write_plot「未成功」宅规不一致
            mem_broken.append(f"记忆重命名未完成（第{old}章→第{new}章）")
            log.warning("resequence 记忆重命名失败（第%s章→第%s章），重排继续",
                        old, new, exc_info=True)
            return False
        return True

    def _heal_phase() -> None:
        """对齐自愈：记忆实际章号 ≠ doc.seq 的残留（历史失败遗留，含两记忆互换
        位置的成环态）经 **transit 号两小趟**归位——直接 rename 会撞
        FileExistsError，先全部挪到保证空置的 transit 号、再全部归位
        （与主两趟同不变量：每步数字域不相交，state.md 锚点不会被别章重映射误伤）。
        开头消化历史残留；逐章容错后末尾再跑一趟，同趟内被占终号失败项在让位后归位。
        """
        todo: list[tuple[ChapterDoc, int]] = []
        for doc, _new in pairs:
            actual = _mem_seq_by_title(store.root, str(doc.meta.get("title") or ""), doc.seq)
            if actual != doc.seq:
                todo.append((doc, actual))
        if not todo:
            return
        base = max([d.seq for d, _ in pairs] + mem.list_chapter_memories() + [0]) + 1
        for i, (doc, actual) in enumerate(todo):
            if _rename_mem(actual, base + i):
                healed.append(f"第{actual}章→第{doc.seq}章")
        for i, (doc, _actual) in enumerate(todo):
            _rename_mem(base + i, doc.seq)

    healed: list[str] = []
    _heal_phase()

    moved = [(d, n) for d, n in pairs if d.seq != n]
    if not moved and not healed and not mem_broken:
        return "resequence_chapters：章节顺序已符合「卷序→情节序」，零变更"

    if moved:
        offset = max(d.seq for d, _ in pairs)

        def _pass(target_of) -> None:
            """一趟章文件更新：逐章写盘（单章失败容错）+ 记忆联动（按标题定位）。"""
            chapters_dir = store.root / "chapters"
            for doc, new in moved:
                old, target = doc.seq, target_of(new)
                if target == old:
                    continue
                title = str(doc.meta.get("title") or doc.seq)
                # 只删**本章**的旧号文件（slug 匹配）：失败重试趟可能与别章同前缀
                # （别章已迁入该号），glob 全删会误伤——同前缀非本章文件绝不碰。
                olds = [p for p in chapters_dir.glob(f"{old:04d}-*.md")
                        if p.stem == f"{old:04d}-{slugify(title)}"]
                # write_chapter 会清掉同目标前缀的其它文件：先把非本章的同前缀文件
                # （坏章等）备份，写完未存活者还原——数据不丢。
                victims = {p: p.read_bytes()
                           for p in chapters_dir.glob(f"{target:04d}-*.md")}
                try:
                    # 文件命名用属性 seq；meta.seq 是持久化口径，两处同改
                    doc.seq = target
                    doc.meta["seq"] = target
                    store.write_chapter(doc)
                except Exception as exc:
                    doc.seq = old                # 回滚内存态：该章保持原位，重跑可自愈
                    doc.meta["seq"] = old
                    ch_broken.append(f"第{old}章→第{target}章文件写入未完成"
                                     f"（{type(exc).__name__}）")
                    log.warning("resequence 章写入失败（第%s章→第%s章），重排继续",
                                old, target, exc_info=True)
                    continue
                for p in olds:  # 旧号文件已整体迁移，残留删掉（前缀不同，不含新文件）
                    p.unlink(missing_ok=True)
                for p, data in victims.items():
                    if not p.exists():
                        p.write_bytes(data)
                # 章已就位再联动记忆；失败不阻断（观测注记，按标题下趟/重跑自愈）
                _rename_mem(_mem_seq_by_title(store.root, title, old), target)

        # 第一趟：old → 中转号（防两章对调时互相覆盖/串号）
        _pass(lambda new: new + offset)
        # 第二趟：中转号 → 最终号
        _pass(lambda new: new)

    _heal_phase()   # 末尾再消化一趟：同趟内被占终号失败项，在别章让位后归位

    if moved:
        lines = [(f"resequence_chapters：重排完成——{len(moved)}/{len(pairs)} 章"
                  "按「卷序→情节序」重编为 1..N（两趟中转防串号）")]
    else:
        lines = ["resequence_chapters：章节顺序已符合「卷序→情节序」（零变更）"]
    if healed:
        lines.append("记忆中转残留已自愈：" + "；".join(healed))
    if ch_broken:
        lines.append("章节写入未完成（不阻断重排，重跑可自愈）：" + "；".join(ch_broken))
    if mem_broken:
        lines.append("记忆联动未完成（不阻断重排，重跑可自愈）：" + "；".join(mem_broken))
    if notes:
        lines.append("跳过：" + "；".join(notes))
    return "\n".join(lines)
