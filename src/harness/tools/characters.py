"""角色卡工具：draft_character（Task 17，起草）+ update_character / delete_character
（P23，修卡/删卡）+ build_characters（P24，自动建卡司）——总设计附录 A.3 本地兑现。

核心在 ``harness.characters``（规划/起草/名册/重提取）；本层只做参数解析 + 卡片读写 +
异常归观测 + 注册同步边界——与 check/memory 等 LLM 工具同一分层纪律（工具层不复制
编排口径）。update/delete 零 LLM（除非名册身份变化触发记忆重提取）；build_characters
规划+起草调 LLM，**落卡过人确认门**（D2 口径：自动建卡司，不自动落卡）。

**P24 本地兑现口径**（与附录 A.3 的差异，见 brief）：
- 「超额并入第一位只补缺不覆盖不拼接」→ **超额新主角降级 major**：并入意味着改动
  已过确认门的既有卡，违背 D2「人确认落卡」更基本；降级信息无损，观测如实说明。
- 确认门 = ask_ui_async 多选（edit_by_command 同款，web 端 pending.md 通用渲染）；
  取消 → 零落卡，卡 JSON 原样附观测（一张不丢）。
- 关系网 / auto review-revise 闭环不在本期（D2：本地无关系对象；人确认代替裁定）。

**P23 本地兑现口径**（与附录 A.3 的差异，见 brief）：
- delete 连带「关系」本地无对象（关系网未落地）；记忆侧经 ``reextract_cast``
  逐章幂等重提；**名册身份变化**（改名/删除/进出 protagonist）才触发，别名/profile
  微调不重提（成本）。
- ``all=true`` 的确认门 = 同传 ``confirm: true`` 参数门（本地工具层无交互确认
  基础设施；reset_outline 的 confirm:true 在本地亦未落地）。
- update_character 的 ``profile`` 只补空缺键、``set_profile`` 逐字段覆盖（同调时
  set 后应用 → 覆盖赢）；``delete=true`` 等价删除。

**观测纪律**：首行确定性文案；失败观测括号内用分类词表（参数/无产出），不混原文
（分类器只扫首行）。

**同步边界**：注册 handler 是同步签名，内部 ``_run_sync`` 驱动（与 draft_character
同款：loop 的 ``_sync_tool`` 线程内 ``_run_sync``，无 running loop → 安全）。
"""
from __future__ import annotations

import asyncio
import json

from harness.characters import (
    MAX_PROTAGONISTS,
    ROLE_GUIDES,
    CastPlanError,
    CharacterDraftError,
    add_protagonist,
    draft_character_card,
    plan_cast,
    remove_protagonist,
    save_character_card,
)
from harness.config import Settings
from harness.llm.client import OpenAICompatClient
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, ApprovalUI, ask_ui_async
from harness.run.events import EventLog
from harness.store.project import ProjectStore
from harness.text import as_text
from harness.tools.base import ToolResult, _run_sync
from harness.tools.registry import ToolRegistry

__all__ = [
    "build_characters",
    "delete_character",
    "draft_character",
    "register_character_tools",
    "update_character",
]


async def draft_character(store: ProjectStore, llm: OpenAICompatClient,
                          prompts: PromptLoader, settings: Settings,
                          args: dict) -> ToolResult:
    """起草角色卡草稿（task.auto_character_create）：args = {name, role_type, requirement?}。

    返回卡片 JSON 草稿（人确认后才落卡——本工具**不写盘**）。参数非法 / LLM 失败
    都归观测（不抛）：参数失败零 LLM 调用。
    """
    name = str(args.get("name") or "").strip()
    role_type = str(args.get("role_type") or "").strip()
    if not name:
        return ToolResult(text="draft_character 失败（参数）：args.name 不能为空", usage=None)
    if role_type not in ROLE_GUIDES:
        return ToolResult(
            text=f"draft_character 失败（参数）：未知 role_type={role_type!r}"
                 f"（可用：{'/'.join(ROLE_GUIDES)}）", usage=None)
    try:
        card = await draft_character_card(store=store, llm=llm, prompts=prompts,
                                          settings=settings, name=name, role_type=role_type,
                                          requirement=str(args.get("requirement") or ""))
    except CharacterDraftError as exc:
        return ToolResult(text=f"draft_character 失败（{exc.reason}）：角色卡草稿生成失败",
                          usage=exc.usage)
    except Exception as exc:  # noqa: BLE001 - 其余异常归观测（分类串不带原文，防瞬时词误判）
        return ToolResult(text=f"draft_character 失败（api:error）：{type(exc).__name__}",
                          usage=None)
    return ToolResult(
        text=("draft_character 完成：角色卡草稿已生成（未经确认不落卡）\n"
              + json.dumps(card, ensure_ascii=False)),
        usage=None)


# ------------------------------------------------------------------ P23：修卡 / 删卡

def _norm_aliases(value: object) -> list[str]:
    """别名规整（附录 A.3「别名数组走 as_text 规整」）：逐项 as_text → 去空白 →
    去空去重保序，落 list[str]（标量/数组/None 全容忍——手改卡常见标量别名）。"""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        text = str(as_text(item) or "").strip()
        if text and text not in out:
            out.append(text)
    return out


async def _refresh_cast_memory(*, store: ProjectStore, llm, prompts, settings) -> str:
    """名册身份变化后的记忆重提取（D2 口径，幂等逐章）；llm=None 跳过并如实说明。"""
    if llm is None:
        return "（记忆未重提取：无 llm）"
    from harness.characters import reextract_cast  # 惰性：顶层 import 成环（同 reextract_cast 内注）
    from harness.memory.store import MemoryStore

    done = await reextract_cast(store=store, mem=MemoryStore(store.root),
                                llm=llm, prompts=prompts, settings=settings)
    return f"（记忆已重提取 {len(done)} 章）"


async def _delete_cards(store: ProjectStore, llm, prompts, settings, names: list[str], *,
                        action: str) -> ToolResult:
    """逐项隔离删卡（update 的 delete=true 与 delete_character 共用）；返回观测。

    每张卡独立读/删：不存在的进跳过清单如实上报；protagonist 卡删除同步滤出
    protagonists.txt；有任何删除即触发记忆重提取（llm=None 如实说明）。
    """
    deleted: list[str] = []
    skipped: list[str] = []
    for name in names:
        try:
            meta = store.read_character(name)
        except FileNotFoundError:
            skipped.append(name)
            continue
        store.delete_character(name)
        deleted.append(name)
        if str(meta.get("role_type") or "") == "protagonist":
            remove_protagonist(store.root, name)

    parts: list[str] = []
    if deleted:
        parts.append(f"已删除 {len(deleted)} 张角色卡（{'、'.join(deleted)}）")
    if skipped:
        parts.append(f"跳过 {len(skipped)} 张（不存在：{'、'.join(skipped)}）")
    if not deleted:
        return ToolResult(text=f"{action} 失败（无产出）：" + "；".join(parts), usage=None)
    note = await _refresh_cast_memory(store=store, llm=llm, prompts=prompts,
                                      settings=settings)
    return ToolResult(text=f"{action} 完成：" + "；".join(parts) + note, usage=None)


async def update_character(store: ProjectStore, llm, prompts, settings,
                           args: dict) -> ToolResult:
    """修角色卡（附录 A.3）：args = {name, new_name?, role_type?, aliases?,
    add_aliases?, remove_aliases?, profile?, set_profile?, arc?, delete?}。

    ``profile`` 只补空缺键、``set_profile`` 逐字段覆盖（同调时 set 后应用）；
    ``delete=true`` 等价删除（连带 protagonists.txt 与记忆重提取）。
    """
    name = str(args.get("name") or "").strip()
    if not name:
        return ToolResult(text="update_character 失败（参数）：args.name 不能为空", usage=None)
    try:
        meta = store.read_character(name)
    except FileNotFoundError:
        return ToolResult(text=f"update_character 失败（参数）：没有角色卡「{name}」", usage=None)
    if args.get("delete"):
        return await _delete_cards(store, llm, prompts, settings, [name],
                                   action="update_character")

    old_role = str(meta.get("role_type") or "")
    changes: list[str] = []

    new_name = str(args.get("new_name") or "").strip()
    role_type = str(args.get("role_type") or "").strip()
    if role_type and role_type not in ROLE_GUIDES:
        return ToolResult(
            text=f"update_character 失败（参数）：未知 role_type={role_type!r}"
                 f"（可用：{'/'.join(ROLE_GUIDES)}）", usage=None)

    profile = meta.get("profile") if isinstance(meta.get("profile"), dict) else {}
    fill = args.get("profile")
    if isinstance(fill, dict) and fill:
        for key, value in fill.items():
            key = str(key)
            if not str(profile.get(key) or "").strip():   # 只补空缺：已有非空不覆盖
                profile[key] = value
        changes.append("profile补缺")
    set_profile = args.get("set_profile")
    if isinstance(set_profile, dict) and set_profile:
        for key, value in set_profile.items():            # 按字段覆盖（改错字用它）
            profile[str(key)] = value
        changes.append("set_profile")
    if profile:
        meta["profile"] = profile

    if args.get("aliases") is not None:                   # 整体替换
        meta["aliases"] = _norm_aliases(args.get("aliases"))
        changes.append("aliases")
    adds = _norm_aliases(args.get("add_aliases"))
    removes = set(_norm_aliases(args.get("remove_aliases")))
    if adds or removes:
        cur = _norm_aliases(meta.get("aliases"))
        cur += [a for a in adds if a not in cur]
        if removes:
            cur = [a for a in cur if a not in removes]
        meta["aliases"] = cur
        changes.append("aliases增减")

    if role_type:
        meta["role_type"] = role_type
        changes.append("role_type")
    if str(args.get("arc") or "").strip():
        meta["arc"] = args["arc"]
        changes.append("arc")

    if not changes and not new_name:
        return ToolResult(text="update_character 失败（参数）：没有可应用的改动"
                              "（profile/set_profile/aliases/add_aliases/remove_aliases/"
                              "role_type/arc/new_name/delete 至少给一项）", usage=None)

    renamed = bool(new_name and new_name != name)
    target = new_name if renamed else name
    if renamed:
        store.rename_character(name, new_name)
        meta["name"] = new_name
        changes.append("new_name")
    store.upsert_character(meta)

    new_role = str(meta.get("role_type") or "")
    if old_role == "protagonist" and (renamed or new_role != "protagonist"):
        remove_protagonist(store.root, name)              # 旧名滤出（改名迁移 / 降出主角）
    if new_role == "protagonist":
        add_protagonist(store.root, target)

    identity_changed = (renamed
                        or (old_role == "protagonist") != (new_role == "protagonist"))
    note = await _refresh_cast_memory(store=store, llm=llm, prompts=prompts,
                                      settings=settings) if identity_changed else ""
    return ToolResult(text=f"update_character 完成：已更新角色卡「{target}」"
                           f"（改动：{'、'.join(changes)}）{note}", usage=None)


async def delete_character(store: ProjectStore, llm, prompts, settings,
                           args: dict) -> ToolResult:
    """删角色卡（附录 A.3，高危）：args = {name?, names?, all?}。

    ``all=true`` 必须同传 ``confirm: true``（确认门=参数门，本地口径见模块 docstring）；
    逐项隔离删除、跳过项如实上报；protagonist 卡删除同步 protagonists.txt；
    有任何删除即触发记忆重提取。
    """
    if args.get("all"):
        if not args.get("confirm"):
            return ToolResult(
                text="delete_character 拒绝：all=true 是高危操作（清空全部角色卡），"
                     "必须同传 confirm: true 才会执行", usage=None)
        names = [c["name"] for c in store.list_characters()]
    else:
        names = _norm_aliases(args.get("name"))
        names += [n for n in _norm_aliases(args.get("names")) if n not in names]
    if not names:
        return ToolResult(text="delete_character 失败（参数）：name / names / all 至少给一项",
                          usage=None)
    return await _delete_cards(store, llm, prompts, settings, names,
                               action="delete_character")


# ------------------------------------------------------------------ P24：自动建卡司

async def build_characters(store: ProjectStore, llm: OpenAICompatClient,
                           prompts: PromptLoader, settings: Settings,
                           ui, args: dict) -> ToolResult:
    """自动建卡司（附录 A.3）：规划（plan_cast）→ 撞名去重 → 主角名额降级 →
    并发起草（Semaphore 3，复用 draft_character_card）→ 人确认门 → 落卡 + 记忆重提。

    args 目前无键（契约 ``build_characters {}``）；``ui=None`` 时自建 ApprovalUI()
    （check/fix.py 同款）。取消（ApprovalCancelled）→ 零落卡，起草卡 JSON 原样附观测。
    """
    del args   # 契约 {}；保留参数位
    if llm is None:
        return ToolResult(text="build_characters 失败（参数）：需要 llm（卡司规划必须调 LLM）",
                          usage=None)
    ui = ui or ApprovalUI()
    try:
        roster, usage = await plan_cast(store=store, llm=llm, prompts=prompts,
                                        settings=settings)
    except CastPlanError as exc:
        return ToolResult(text=f"build_characters 失败（{exc.reason}）：卡司规划失败",
                          usage=exc.usage)

    # 撞名去重：既有名册 name + aliases 命中即弃（plan_cast 已按 name 去重）
    existing = store.list_characters()
    taken: set[str] = set()
    for card in existing:
        taken.add(str(card.get("name")))
        taken |= {str(a) for a in (card.get("aliases") or [])}
    fresh: list[dict] = []
    dupes: list[str] = []
    for entry in roster:
        if entry["name"] in taken:
            dupes.append(entry["name"])
        else:
            fresh.append(entry)
    if not fresh:
        note = f"（撞名跳过 {len(dupes)}：{'、'.join(dupes)}）" if dupes else ""
        return ToolResult(text=f"build_characters 完成：无需新建角色{note}", usage=usage)

    # 主角名额：slots = max(0, MAX_PROTAGONISTS − 既有主角数)；超额新主角降级 major
    # （本地裁定：不并入既有卡——并入=改动已过确认门的卡，违背 D2 更基本，见 brief）
    existing_prot = sum(1 for c in existing if c.get("role_type") == "protagonist")
    slots = max(0, MAX_PROTAGONISTS - existing_prot)
    demoted: list[str] = []
    used = 0
    for entry in fresh:
        if entry["role_type"] != "protagonist":
            continue
        if used < slots:
            used += 1
        else:
            entry["role_type"] = "major"
            demoted.append(entry["name"])

    # 并发起草：单卡失败（CharacterDraftError 等）不拖垮批，进起草失败清单
    sem = asyncio.Semaphore(3)

    async def _draft(entry: dict):
        async with sem:
            try:
                card = await draft_character_card(
                    store=store, llm=llm, prompts=prompts, settings=settings,
                    name=entry["name"], role_type=entry["role_type"],
                    requirement=entry.get("requirement", ""))
            except Exception as exc:  # noqa: BLE001 - 归批级清单（首行不混原文）
                return entry, None, exc
            return entry, card, None

    results = await asyncio.gather(*[_draft(e) for e in fresh])
    cards: list[dict] = []
    failed: list[str] = []
    for entry, card, _exc in results:
        if card is None:
            failed.append(entry["name"])
        else:
            cards.append(card)
    if not cards:
        return ToolResult(text=f"build_characters 失败（无产出）：{len(failed)} 张卡起草全部失败"
                              f"（{'、'.join(failed)}）", usage=usage)

    # 人确认门（edit_by_command 同款；web 端 pending.md 通用渲染，前端零改动）
    options = [f"落卡：{c['name']}（{ROLE_GUIDES[c['role_type']][0]}）" for c in cards]
    lines = []
    for c in cards:
        alias = f"｜别名：{'、'.join(c['aliases'])}" if c.get("aliases") else ""
        personality = str(c.get("profile", {}).get("personality") or "")[:40]
        lines.append(f"- {c['name']}（{ROLE_GUIDES[c['role_type']][0]}）{alias}｜{personality}")
    question = (f"build_characters 起草了 {len(cards)} 张角色卡"
                f"（空格分隔可多选；取消 = 零落卡）：\n" + "\n".join(lines))
    try:
        choice = await ask_ui_async(ui, question, options, multi=True)
    except ApprovalCancelled:
        return ToolResult(text=("build_characters：已取消，零落卡（起草卡原样保留，"
                                "可人工落卡）：\n"
                                + json.dumps(cards, ensure_ascii=False)), usage=usage)
    approved_idx = {i for i in choice.indices if 0 <= i < len(cards)}
    approved = [c for i, c in enumerate(cards) if i in approved_idx]

    parts = [f"落卡 {len(approved)}（{'、'.join(c['name'] for c in approved)}）"]
    parts.append(f"未选 {len(cards) - len(approved)}")
    parts.append(f"起草失败 {len(failed)}" + (f"（{'、'.join(failed)}）" if failed else ""))
    parts.append(f"撞名跳过 {len(dupes)}" + (f"（{'、'.join(dupes)}）" if dupes else ""))
    parts.append(f"主角降级 {len(demoted)}" + (f"（{'、'.join(demoted)}）" if demoted else ""))
    note = ""
    if approved:
        events = EventLog(store.root)
        for card in approved:
            save_character_card(store, events, card)
        note = await _refresh_cast_memory(store=store, llm=llm, prompts=prompts,
                                          settings=settings)
    return ToolResult(text="build_characters 完成：" + "；".join(parts) + note,
                      usage=usage)


def register_character_tools(reg: ToolRegistry, *, store: ProjectStore,
                             llm: OpenAICompatClient, prompts: PromptLoader,
                             settings: Settings, ui=None) -> None:
    """注册角色卡四工具（均 read_only=False：draft/build 调 LLM；update/delete/build 写盘）。"""

    def handler_draft(store_: ProjectStore, args: dict) -> str:
        return _run_sync(draft_character(store_, llm, prompts, settings, args)).text

    def handler_update(store_: ProjectStore, args: dict) -> str:
        return _run_sync(update_character(store_, llm, prompts, settings, args)).text

    def handler_delete(store_: ProjectStore, args: dict) -> str:
        return _run_sync(delete_character(store_, llm, prompts, settings, args)).text

    def handler_build(store_: ProjectStore, args: dict) -> str:
        return _run_sync(build_characters(store_, llm, prompts, settings, ui, args)).text

    reg.register("draft_character", handler_draft, read_only=False)
    reg.register("update_character", handler_update, read_only=False)
    reg.register("delete_character", handler_delete, read_only=False)
    reg.register("build_characters", handler_build, read_only=False)
