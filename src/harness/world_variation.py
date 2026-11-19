"""世界坐标（P37，C3 子项①；seed `_world_variation` 本地移植，设计 §1.3 ② + 附录 B.5）。

**为什么**：模型对同题材有很强先验，同一个模型既写又评会**自我确认**（seed 实测：写西幻
就反复落到「债务／赎罪券／什一税」一族，换项目也一样）。所以坐标机制 = ① 多候选扩大取材面
② 跨 provider 评审破同源偏见 ③ 代码层确定性兜底（任何一步失败都不至于没有坐标）。

**seed 语义逐字移植**（web_dream autopilot_runner.py L1807-2088），本地差三处：
- 持久化：`Project.settings["world_variation"]` → `<root>/.harness/world_variation.json`；
  项目自定义黑名单 `settings["banned_tropes"]` → `<root>/.harness/banned_tropes.json`。
- 评审 provider 选择：本地无 `routing.judge_provider` 配置项 → `settings.providers` 里
  第一个 ≠ default_provider 的；一个都没有 → None（seed 的「同源就地自查」本地不做——
  单 provider 自查无意义，直接确定性选优）。
- 触发：seed 挂在 autopilot 自动链；本地是 REPL `/worldcore [需求]` 用户显式触发
  （agent 自决会绕过用户，不符合 D2），坐标生成后经 edit_by_command 的世界目标
  提示词注入【本次创作坐标…】硬约束块。
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.config import Settings
    from harness.llm.client import OpenAICompatClient
    from harness.prompts.loader import PromptLoader
    from harness.store.project import ProjectStore

from harness.config import ProviderConfig  # runtime isinstance 用；注解走 __future__ 惰性

__all__ = [
    "clear_variation",
    "collect_used",
    "draft_candidates",
    "judge_pick",
    "load_banned_tropes",
    "pick_judge_config",
    "read_variation",
    "resolve_variation",
    "variation_block",
    "write_variation",
]

log = logging.getLogger("world_variation")

#: 坐标持久化（seed 的 Project.settings["world_variation"] 换成本地文件）
VARIATION_FILE = Path(".harness") / "world_variation.json"
#: 项目自定义套路黑名单（seed 的 settings["banned_tropes"]）
BANNED_FILE = Path(".harness") / "banned_tropes.json"

# ------------------------------------------------------------------ seed 逐字常量
# （autopilot_runner.py L1810-1848；注释保留 seed 的事故记录）

#: 世界观「创作坐标」的四个维度（key 用于持久化与跨项目避重，label 用于渲染提示词）。
#: 已剔除重复度高的「债务/账目」一族（以债务为媒 / 秩序靠不断偿还维持 / 账目）——实测它们反复撞车，
#: 让不同项目收敛成同一套「欠债—还债」设定；这里换成取材面更宽的选项。
_VARIATION_AXES: list[tuple[str, str, tuple[str, ...]]] = [
    ("metaphor", "世界的本质隐喻/取材方向",
     ("潮汐", "锈蚀", "歌谣", "伤口", "回信", "根系", "温差", "织机", "盐", "迁徙",
      "镜像", "回声", "年轮", "发酵", "蜂群", "雾季", "断桥", "胎记", "琥珀", "纺锤")),
    ("medium", "超自然力量的存在介质",
     ("以器物为媒", "以言语为媒", "以血脉为媒", "以土地为媒", "以记忆为媒",
      "以自然征候为媒", "以技艺为媒", "以名字为媒", "以光影为媒", "以气味为媒")),
    ("godstate", "神明的存在状态（故事有神明时才用）",
     ("沉睡", "隐退缺席", "被放逐", "正在退化", "寄生于人或物", "彼此分裂内耗",
      "被自身律法束缚", "早已更替换代", "从未真正存在", "已被人类遗忘")),
    ("cost", "全书的核心代价/张力",
     ("力量越用越少", "记忆可被交易", "地理会自行移动", "时间流速不均", "语言即权力",
      "以寿命/身体为价", "真相被系统性掩盖", "名字会被遗忘", "情感会被磨损",
      "被注视即有代价", "越接近真相越孤立")),
]

#: 模型对同题材的默认惯性（实测：写西幻就反复落到「债务／赎罪券／什一税」这一族，换项目也一样）。
#: 这是**默认黑名单**：候选里命中即淘汰；项目可用 `banned_tropes.json` 追加自己的。
_DEFAULT_BANNED_TROPES: tuple[str, ...] = (
    "债务", "借债", "欠债", "还债", "债契", "债主", "抵债", "赎罪券",
    "按揭", "什一税", "账目", "利息", "欠条", "赎买",
)
_CORE_CANDIDATES = 6   # 一次产几套内核候选供评审

#: 需求里要「经典款」的信号词——此时**不要**追求原创意象，那会与需求直接打架。
#: 真实事故：用户要「最经典款的剑与魔法」，我们却按创作坐标硬推「核心意象必须独创、禁止默认套路」，
#: 于是连出 骨髓 → 锈 → 痛 → 光影 四套自造体系；员工每轮都判断「不够经典」→ 全删重建，空转 5 轮。
_CANON_INTENT: tuple[str, ...] = (
    "经典", "正统", "最经典", "标准款", "最标准", "传统", "教科书", "范例", "原汁原味",
)

_CANON_BLOCK = (
    "\n【本次**不追求原创意象**（需求明确要「经典款／正统」）】\n"
    "需求要的是**经典／正统／标准**的该题材世界观——请直接采用该题材的**公认正典**要素："
    "力量体系与职业划分、种族、诸神与教会、封建等级与势力、冒险者公会与日常质感这类"
    "读者一看就熟的标准配置；可参考【已有联网核查结论】里核实过的正典细节。\n"
    "**不要**为了「新颖」自造一套独创意象体系去替代它，也不要给每个维度各换一个自造的隐喻名。"
)


# ------------------------------------------------------------------ 纯函数（seed 逐字）

def _norm(text: str | None) -> str:
    return "".join((text or "").split())


def _hits(text: str, words) -> list[str]:
    return [w for w in words if w and w in (text or "")]


def _core_repeated(core: str, used_cores: list[str]) -> bool:
    """与作者旧作内核重复/近义（互相包含且足够长）即算重复。"""
    c = _norm(core)
    if len(c) < 6:
        return False
    return any(len(u) >= 6 and (c in u or u in c) for u in (_norm(x) for x in used_cores))


def _wants_canon(*texts: str) -> bool:
    text = "\n".join(t or "" for t in texts)
    return any(w in text for w in _CANON_INTENT)


def _cand_text(c: dict) -> str:
    return " ".join(str(c.get(k) or "") for k in
                    ("core", "metaphor", "medium", "godstate", "cost", "why"))


def _rule_pick(cands: list[dict], used: dict[str, set[str]],
               rnd: random.Random) -> dict:
    """确定性选优（评审不可用/结果无效时的兜底）：优先挑与作者旧作重合最少的一套。"""

    def overlap(c: dict) -> int:
        return sum(1 for key, vals in used.items() if str(c.get(key) or "") in vals)

    best = min(overlap(c) for c in cands)
    return dict(rnd.choice([c for c in cands if overlap(c) == best]))


def _rule_variation(project_key: str, used: dict[str, set[str]]) -> dict:
    """最后的兜底：从清理过的固定轴池里确定性取一组（跨项目逐轴避重）。"""
    rnd = random.Random(project_key or "0")
    return {key: rnd.choice([o for o in options if o not in used[key]] or list(options))
            for key, _label, options in _VARIATION_AXES}


def _render_variation(picks: dict) -> str:
    """把坐标渲染成**硬约束**块（不是「可选启发」）。

    真实事故：坐标措辞写成「仅作取材启发…与需求无关直接忽略」后，模型一律当没看见，仍旧照默认
    套路写（七柱神系 / 通用创世），不同项目收敛到同一套设定——坐标形同虚设。
    """
    lines = ([f"- 世界内核（一句话命题）：{picks['core']}"] if picks.get("core") else [])
    lines += [f"- {label}：{picks[key]}" for key, label, _ in _VARIATION_AXES if picks.get(key)]
    if picks.get("why"):
        lines.append(f"- 选它的理由：{picks['why']}")
    return ("\n【本次创作坐标（**硬约束**：本书世界的核心意象必须从这里长出来，不得改用默认套路）】\n"
            + "\n".join(lines)
            + "\n用法：把坐标**具体化**为专有名词、规则与代价，让世界的创世/力量/信仰/社会都围绕它展开；"
              "**禁止**另起一套通用设定（如模型默认的元素等级制、几柱神系、通用创世神话）来顶替坐标，"
              "同题材也要写出这一本独有的世界。坐标只在其对应维度**确属【需求】范围**时才落地"
              "（故事没有神明就不写神系），且不得违背【需求】【限制】。"
              "**代价、信仰、经济一律用坐标的意象来表达，不要再落回「借债/还债/赎罪券/按揭/什一税」"
              "这一类默认财务套路**——那是最省事的模型惯性，读者一眼就觉得每个世界都是同一套。")


# ------------------------------------------------------------------ 持久化

def read_variation(root: Path) -> dict:
    """读 `<root>/.harness/world_variation.json`；缺文件/坏档 → {}（当无坐标，不炸）。"""
    path = Path(root) / VARIATION_FILE
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("世界坐标文件损坏（按无坐标处理）：%s（%s）", path, exc)
        return {}
    return dict(value) if isinstance(value, dict) else {}


def write_variation(root: Path, picks: dict) -> None:
    path = Path(root) / VARIATION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(picks, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def clear_variation(root: Path) -> None:
    path = Path(root) / VARIATION_FILE
    if path.exists():
        path.unlink()


def load_banned_tropes(root: Path) -> list[str]:
    """禁用套路清单：默认黑名单 + 项目自定义（`banned_tropes.json`，去重保序）。"""
    out = list(_DEFAULT_BANNED_TROPES)
    path = Path(root) / BANNED_FILE
    if path.is_file():
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("项目黑名单损坏（只用默认黑名单）：%s（%s）", path, exc)
            extra = []
        for t in extra if isinstance(extra, list) else []:
            t = str(t).strip()
            if t and t not in out:
                out.append(t)
    return out


def collect_used(library_root: Path, except_root: Path | None = None
                 ) -> tuple[dict[str, set[str]], list[str]]:
    """库内**其它项目**已用过的内核/坐标值——同一位作者的新书要避开旧书用过的题材套路。"""
    used: dict[str, set[str]] = {key: set() for key, _, _ in _VARIATION_AXES}
    cores: list[str] = []
    root = Path(library_root)
    if not root.is_dir():
        return used, cores
    exc = Path(except_root).resolve() if except_root is not None else None
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if exc is not None and child.resolve() == exc:
            continue
        v = read_variation(child)
        for key, _label, _options in _VARIATION_AXES:
            if v.get(key):
                used[key].add(str(v[key]))
        if v.get("core"):
            cores.append(str(v["core"]))
    return used, cores


# ------------------------------------------------------------------ LLM 两步

def pick_judge_config(settings: Settings) -> ProviderConfig | None:
    """评审 provider：本地无 routing.judge_provider 配置项 → `providers` 里第一个
    ≠ default_provider 的；一个都没有 → None（走确定性兜底）。

    「另一个 Provider 当评审」是针对**同源偏见**的：同一个模型既写又评，它的题材惯性会自我确认。
    """

    providers = getattr(settings, "providers", None) or {}
    default = str(getattr(settings, "default_provider", "") or "")
    for name, cfg in providers.items():
        if name != default and isinstance(cfg, ProviderConfig):
            return cfg
    return None


async def draft_candidates(*, llm: OpenAICompatClient, prompts: PromptLoader,
                           premise: str, constraints: str, used: str,
                           banned: str) -> list[dict] | None:
    """task.auto_world_core_draft（standard/1.0/8192）→ 候选列表；失败/全无效 → None。"""
    from harness.tools.base import complete_structured

    def _extract(text: str):
        from harness.tools.base import extract_json
        value = extract_json(text)
        if not isinstance(value, dict):
            return None
        cands = value.get("candidates")
        if not isinstance(cands, list):
            return None
        out = [c for c in cands
               if isinstance(c, dict) and str(c.get("core") or "").strip()]
        return out or None

    try:
        value, _usage, _reason = await complete_structured(
            llm, model=llm.provider.standard, temperature=1.0, max_tokens=8192,
            prompt=prompts.render("task.auto_world_core_draft", n=str(_CORE_CANDIDATES),
                                  premise=premise, constraints=constraints,
                                  used=used, banned=banned),
            extract=_extract)
    except Exception as exc:  # noqa: BLE001 - 候选生成失败不阻断，回退确定性坐标
        log.warning("世界内核候选生成失败：%s", exc)
        return None
    if value is None:
        return None
    return [dict(c) for c in value]


async def judge_pick(*, llm: OpenAICompatClient, prompts: PromptLoader,
                     cands: list[dict], premise: str, constraints: str,
                     used: str, banned: str, judge_tag: str) -> dict | None:
    """task.auto_world_core_judge（pro/0.2/4096）→ 选优；失败/chosen 越界 → None。

    `llm` 是**评审方**的 client（provider 必须与生成方不同，由调用方保证）。
    """
    from harness.tools.base import complete_structured, extract_json

    cand_text = "\n".join(
        f"[{i}] core={c.get('core')}；metaphor={c.get('metaphor')}；medium={c.get('medium')}；"
        f"godstate={c.get('godstate')}；cost={c.get('cost')}；why={c.get('why')}"
        for i, c in enumerate(cands))

    def _extract(text: str):
        value = extract_json(text)
        if not isinstance(value, dict) or not isinstance(value.get("reviews"), list):
            return None
        return value

    try:
        value, _usage, _reason = await complete_structured(
            llm, model=llm.provider.pro, temperature=0.2, max_tokens=4096,
            prompt=prompts.render("task.auto_world_core_judge", premise=premise,
                                  constraints=constraints, used=used, banned=banned,
                                  candidates=cand_text),
            extract=_extract)
    except Exception as exc:  # noqa: BLE001 - 评审失败不阻断，退回确定性选优
        log.warning("世界内核评审失败：%s", exc)
        return None
    if value is None:
        return None
    try:
        idx = int(value.get("chosen"))
    except (TypeError, ValueError):
        return None
    if not 0 <= idx < len(cands):
        return None
    pick = dict(cands[idx])
    pick["judge_provider"] = judge_tag
    pick["judge_reason"] = str(value.get("reason") or "")[:150]
    return pick


# ------------------------------------------------------------------ 主编排

async def _draft_and_judge(*, store: ProjectStore, settings: Settings,
                           llm: OpenAICompatClient, judge_llm: OpenAICompatClient | None,
                           prompts: PromptLoader, premise: str, constraints: str,
                           banned: list[str], used: dict[str, set[str]],
                           used_cores: list[str]) -> dict | None:
    """① 一次产出多套内核候选 → ② 确定性淘汰套路/重复 → ③ 跨 Provider 评审选优。"""
    used_text = "；".join(used_cores) or "（无——这是该作者的第一本书）"
    banned_text = "、".join(banned)
    try:
        cands = await draft_candidates(llm=llm, prompts=prompts, premise=premise,
                                       constraints=constraints, used=used_text,
                                       banned=banned_text)
    except Exception as exc:  # noqa: BLE001 - 候选生成失败不阻断，回退确定性坐标
        log.warning("世界内核候选生成失败，回退确定性坐标：%s", exc)
        return None
    if not cands:
        return None
    # 确定性预筛：命中黑名单 / 与作者旧作内核重复的**直接淘汰**（不指望模型自己守规矩）
    cands = [c for c in cands if not _hits(_cand_text(c), banned)]
    cands = [c for c in cands if not _core_repeated(str(c.get("core") or ""), used_cores)]
    if not cands:
        log.info("世界内核候选全部被黑名单/重复淘汰，回退确定性坐标")
        return None
    if len(cands) > 1 and judge_llm is not None:
        judged = await judge_pick(llm=judge_llm, prompts=prompts, cands=cands,
                                  premise=premise, constraints=constraints,
                                  used=used_text, banned=banned_text,
                                  judge_tag=judge_llm.provider.pro)
        if judged is not None:
            return judged
    return _rule_pick(cands, used, random.Random(str(store.root)))


async def resolve_variation(*, store: ProjectStore, settings: Settings,
                            llm: OpenAICompatClient, prompts: PromptLoader,
                            premise: str, constraints: str,
                            extra: str = "",
                            judge_llm: OpenAICompatClient | None = None,
                            _close_judge: bool = True) -> tuple[dict | None, str]:
    """本次世界观构建的「创作坐标/世界内核」——一组**硬约束**的取材维度。

    短路链（seed 逐字语义）：
    ① 需求明确要「经典款」→ 落 ``{"canon": true}`` 并返回 _CANON_BLOCK
      （正典优先于已持久化的坐标——「裁缝反复复活」事故）；
    ② 已有坐标 → 原样渲染（同项目复跑坐标不变）；
    ③ **世界有条目 → 不生成**，返回 (None, "")（用户手写/导入的条目就是这本书的坐标——
      「潮汐坐标硬约束西幻预设」事故）；否则 draft → 黑名单/旧作重复淘汰 → 跨 provider
      评审 → ``_rule_pick``（与旧作重合最少）→ ``_rule_variation``（四轴池兜底）→ 落盘。

    ``judge_llm`` 缺省时按 :func:`pick_judge_config` 自建评审 client（用完关闭）；
    测试经 ``judge_llm`` 注入 FakeLLM（``_close_judge=False``）。
    返回 (picks, block)；``picks is None`` = 未生成（世界非空）。
    """
    root = store.root
    stored = read_variation(root)
    canon_now = _wants_canon(premise, extra, constraints)
    canon_sticky = isinstance(stored, dict) and bool(stored.get("canon"))
    if canon_now or canon_sticky:
        if not canon_sticky:
            write_variation(root, {
                "canon": True, "why": "需求明确要「经典款」：创作坐标让位给题材正典"})
        return read_variation(root), _CANON_BLOCK
    if isinstance(stored, dict) and (stored.get("core")
                                     or any(stored.get(k) for k, _, _ in _VARIATION_AXES)):
        return stored, _render_variation(stored)
    # 世界里已有设定条目 → 不生成坐标：用户（手写/导入预设/助手补写）的内容就是这本书的坐标。
    if store.read_world():
        return None, ""
    banned = load_banned_tropes(root)
    used, used_cores = collect_used(getattr(settings, "library_root", root), root)

    judge = judge_llm
    built = None
    if judge is None:
        cfg = pick_judge_config(settings)
        if cfg is not None:
            from harness.llm.client import OpenAICompatClient
            built = OpenAICompatClient(cfg)
            judge = built
    try:
        picks = await _draft_and_judge(store=store, settings=settings, llm=llm,
                                       judge_llm=judge, prompts=prompts,
                                       premise=premise, constraints=constraints,
                                       banned=banned, used=used, used_cores=used_cores)
    finally:
        if built is not None and _close_judge:
            await built.aclose()
    if picks is None:
        picks = _rule_variation(str(root), used)
    write_variation(root, picks)
    return picks, _render_variation(picks)


def variation_block(root: Path) -> str:
    """注入侧只读 helper：有坐标渲染坐标块（canon → _CANON_BLOCK），无坐标 → ""。"""
    stored = read_variation(root)
    if isinstance(stored, dict) and stored.get("canon"):
        return _CANON_BLOCK
    if isinstance(stored, dict) and (stored.get("core")
                                     or any(stored.get(k) for k, _, _ in _VARIATION_AXES)):
        return _render_variation(stored)
    return ""


def readme_premise(root: Path) -> str:
    """REPL `/worldcore` 的 premise 缺省：README 的题材/基调/结局三行（没有则空串）。"""
    path = Path(root) / "README.md"
    if not path.is_file():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for label in ("题材", "基调", "结局"):
            if line.strip().startswith(f"{label}："):
                lines.append(line.strip())
    return "；".join(lines)
