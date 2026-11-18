"""Skills 机制（P5 Task 6，设计 §2.6）：两级 SKILL.md 扫描器 + 定向注入映射 + 两个只读工具。

**两级作用域**（设计 §2.6 拍板）：

- 用户级：``~/.harness/skills/<name>/SKILL.md`` —— 跨项目通用写作方法论；
- 项目级：``<项目>/.harness/skills/<name>/SKILL.md`` —— 仅本项目，**同名覆盖用户级**。

**SKILL.md 格式**（与 Claude Code skills 兼容）：frontmatter ``name``/``description``
+ markdown 正文。frontmatter 解析复用 ``store/frontmatter.py`` 的 **``split_document``
（纯函数）**——**不用** ``split_file``：后者解析失败会给原文件写 ``.bak`` 备份，
扫描用户目录里的第三方 skill 时产生副作用（用户不该被扫描动作改文件）。

容错纪律：坏 frontmatter → **跳过该 skill**（log.warning 点名），其余 skill 照常返回；
无 frontmatter → name 取目录名、description 取空串。

**两种载入方式**（设计 §2.6）：

1. ``skill_injection(root, task_key)`` —— 代码定向载入（固化在流水线里，见
   ``TARGETED_INJECT``）；无 skill / 无映射 → 返回 ``""``（**字节级零变化**）；
2. ``list_skills`` / ``read_skill`` —— 员工工具（零 LLM 只读），模型想查方法论时自己整篇读入。
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from harness.store.frontmatter import split_document
from harness.store.project import ProjectStore

__all__ = [
    "TARGETED_INJECT",
    "Skill",
    "copy_skill",
    "import_target",
    "list_skills",
    "load_skill",
    "load_skill_by_dir",
    "read_skill",
    "resolve_import",
    "scan_skills",
    "skill_injection",
    "user_skills_dir",
]

log = logging.getLogger("skills")

#: skill 文件名（目录内的 SKILL.md）
SKILL_FILE = "SKILL.md"

#: 项目级 skills 目录（相对项目根）
PROJECT_SKILLS_DIR = Path(".harness") / "skills"

#: 定向注入映射：任务提示词 key → skill 名（裁定 17：三键——本地实际存在的三个 render 点；
#: auto_build_world/auto_world_expand/auto_character_create 三工具本地不存在，世界/角色创建
#: 实际走 edit_by_command，故挂点为 task.auto_modify_propose）。
TARGETED_INJECT: dict[str, str] = {
    "task.auto_master_plan": "master_plan",
    "task.auto_plan_plots": "plan_plots",
    "task.auto_modify_propose": "world_building",
}


@dataclass
class Skill:
    """一个 skill：frontmatter 名/描述 + 作用域 + 文件路径 + SKILL.md 全文（含 frontmatter）。"""

    name: str
    description: str
    scope: str          # "project" | "user"
    path: Path
    body: str


def user_skills_dir() -> Path:
    """用户级 skills 目录（**调用期求值**——``Path.home()`` 不得在 import 时定死）。"""
    return Path.home() / ".harness" / "skills"


# ------------------------------------------------------------------ 扫描（内部）

def _read_skill(path: Path, scope: str, default_name: str) -> Skill | None:
    """读一个 SKILL.md → Skill；读失败 / frontmatter 坏 → None（跳过该 skill，不炸）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("skill 读取失败，已跳过：%s（%s）", path, exc)
        return None
    try:
        meta, _body = split_document(text)
    except (yaml.YAMLError, ValueError) as exc:
        log.warning("skill frontmatter 解析失败，已跳过：%s（%s）", path, exc)
        return None
    name = str(meta.get("name") or "").strip() or default_name
    description = str(meta.get("description") or "").strip()
    return Skill(name=name, description=description, scope=scope, path=path, body=text)


def _scan_dir(base: Path, scope: str) -> dict[str, Skill]:
    """扫一级目录下的 ``*/SKILL.md`` → {name: Skill}（同名后者覆盖前者）。"""
    found: dict[str, Skill] = {}
    if not base.is_dir():
        return found
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / SKILL_FILE
        if not path.is_file():
            continue
        skill = _read_skill(path, scope, entry.name)
        if skill is not None:
            found[skill.name] = skill
    return found


# ------------------------------------------------------------------ 公开 API

def scan_skills(store_root: Path, user_dir: Path | None = None) -> dict[str, Skill]:
    """两级扫描 → ``{name: Skill}``；**项目级覆盖用户级同名**。

    ``user_dir`` 缺省 = ``user_skills_dir()``（测试经参数注入临时目录）。

    **目录遍历**——只给「列清单」用（``list_skills``）；每拍调用的注入路径与 style 钩子
    走 ``load_skill_by_dir``（直读那一个文件），不在事件循环里重扫全部 SKILL.md。
    """
    user_base = Path(user_dir) if user_dir is not None else user_skills_dir()
    found = _scan_dir(user_base, "user")
    found.update(_scan_dir(Path(store_root) / PROJECT_SKILLS_DIR, "project"))
    return found


def _load_by_dir(base: Path, scope: str, key: str) -> Skill | None:
    """按**目录名**直读那一个 SKILL.md（``<base>/<key>/SKILL.md``）——不遍历目录。"""
    path = Path(base) / key / SKILL_FILE
    if not path.is_file():
        return None
    return _read_skill(path, scope, key)


def load_skill_by_dir(store_root: Path, name: str, user_dir: Path | None = None, *,
                      scopes: tuple[str, ...] = ("project", "user")) -> Skill | None:
    """按**目录名**取一个 skill（项目级优先、用户级兜底）——**不遍历目录**。

    注入路径（``skill_injection``）与 style 钩子走这里：``scan_skills`` 是目录遍历，
    每拍在事件循环里重扫全部 SKILL.md 违反 P4 I-1 纪律。
    目录名即查找键——目录名是 ``style`` 但 frontmatter 写了别的名字时，文风钩子
    **不得静默失效**（M-12）。
    """
    key = str(name or "").strip()
    if not key:
        return None
    user_base = Path(user_dir) if user_dir is not None else user_skills_dir()
    bases: list[tuple[Path, str]] = []
    if "project" in scopes:
        bases.append((Path(store_root) / PROJECT_SKILLS_DIR, "project"))
    if "user" in scopes:
        bases.append((user_base, "user"))
    for base, scope in bases:
        skill = _load_by_dir(base, scope, key)
        if skill is not None:
            return skill
    return None


def load_skill(store_root: Path, name: str, user_dir: Path | None = None, *,
               scopes: tuple[str, ...] = ("project", "user")) -> Skill | None:
    """按名取一个 skill（项目级优先）；找不到 → None。

    查找键 = frontmatter name（扫描口径，项目级覆盖用户级同名）；**扫不到再按目录名找
    一次**（M-12：目录名是 style 但 frontmatter 写了别的名字 → 文风钩子不得静默失效）。

    ``scopes`` 限定可见作用域——文风 skill 只认项目级（设计 §2.6：用户级 style 不注入写作）。

    **name 命中但 scope 不符**时**继续走目录名兜底**（而不是直接 None）：用户级有个
    ``name=style`` 的 skill、项目级有个**目录** ``style``（frontmatter name 不同）时，
    ``scopes=("project",)`` 不得直接返回 None（那正是「静默失效」）。
    兜底**只救项目级**（scope 过滤的语义是「本项目自己的 skill」；用户级同名项不因
    scope 不符而上位），且兜底结果同样要落在 ``scopes`` 内。
    """
    key = str(name or "").strip()
    if not key:
        return None
    skill = scan_skills(store_root, user_dir).get(key)
    if skill is not None:
        if skill.scope in scopes:
            return skill
        fallback = load_skill_by_dir(store_root, key, user_dir, scopes=("project",))
        return fallback if fallback is not None and fallback.scope in scopes else None
    # 名称查不到 → 按目录名兜底（项目级优先、用户级兜底）
    return load_skill_by_dir(store_root, key, user_dir, scopes=scopes)


def _skill_by_meta_name(base: Path, scope: str, key: str) -> Skill | None:
    """按 **frontmatter name** 找一个 skill（**兜底**：目录名直读落空时）。

    目录遍历范围 = ``base`` 的一级子目录；只在「目录名找不到」时才走这里（正常布局
    ——目录名 == frontmatter name——零遍历）。
    """
    if not base.is_dir():
        return None
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / SKILL_FILE
        if not path.is_file():
            continue
        skill = _read_skill(path, scope, entry.name)
        if skill is not None and skill.name == key:
            return skill
    return None


def _load_by_meta_name(store_root: Path, name: str, user_dir: Path | None = None, *,
                       scopes: tuple[str, ...] = ("project", "user")) -> Skill | None:
    """按 frontmatter name 取一个 skill（项目级优先、用户级兜底）——目录名兜底的兜底。"""
    key = str(name or "").strip()
    if not key:
        return None
    user_base = Path(user_dir) if user_dir is not None else user_skills_dir()
    bases: list[tuple[Path, str]] = []
    if "project" in scopes:
        bases.append((Path(store_root) / PROJECT_SKILLS_DIR, "project"))
    if "user" in scopes:
        bases.append((user_base, "user"))
    for base, scope in bases:
        skill = _skill_by_meta_name(base, scope, key)
        if skill is not None:
            return skill
    return None


def skill_injection(root: Path, task_key: str, user_dir: Path | None = None) -> str:
    """定向注入块：查 ``TARGETED_INJECT`` → 取 skill → 渲染；无映射 / 无 skill → ``""``。

    渲染口径：``"\\n\\n【技能：{name}】\\n{body}"``（body = SKILL.md 全文含 frontmatter）。
    返回 ``""`` 时调用点**字节级零变化**——现有测试必须原样全绿。

    查找键**先目录名、后 frontmatter name**（两者命中其一即算找到）：目录名优先保证
    「目录即身份」的直觉（M-12），frontmatter name 兜底保住旧行为——目录名与
    frontmatter name 不同时，注入不得静默消失（同一类「静默失效」）。
    """
    skill_name = TARGETED_INJECT.get(task_key)
    if not skill_name:
        return ""
    skill = load_skill_by_dir(root, skill_name, user_dir)
    if skill is None:
        skill = _load_by_meta_name(root, skill_name, user_dir)
    if skill is None:
        return ""
    return f"\n\n【技能：{skill.name}】\n{skill.body}"


# ------------------------------------------------------------------ 只读工具（零 LLM）

def list_skills(store: ProjectStore, args: dict) -> str:
    """两级 skill 清单：每行 ``name（scope）：description``；空库如实「无可用 skill」。"""
    found = scan_skills(store.root)
    if not found:
        return "无可用 skill"
    return "\n".join(
        f"{found[name].name}（{found[name].scope}）：{found[name].description or '（无描述）'}"
        for name in sorted(found))


def read_skill(store: ProjectStore, args: dict) -> str:
    """取一个 skill 的 SKILL.md 全文；未找到如实「无此 skill：<name>」。

    **先按目录名直读、再按 name 兜底**（与注入路径同口径）：目录名命中时零目录遍历。
    """
    name = str(args.get("name") or "").strip()
    if not name:
        return "read_skill 需要 args.name（skill 名），当前为空"
    skill = load_skill_by_dir(store.root, name) or load_skill(store.root, name)
    if skill is None:
        return f"无此 skill：{name}"
    return skill.body


# ------------------------------------------------------------------ 外部导入（/skill import）

def _frontmatter_name(path: Path) -> str:
    """读 SKILL.md 的 frontmatter name（无 / 坏 frontmatter → 空串，交调用方兜底）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        meta, _body = split_document(text)
    except (yaml.YAMLError, ValueError):
        return ""
    return str(meta.get("name") or "").strip()


#: Windows 保留设备名（忽略大小写与扩展名）：CON/PRN/AUX/NUL/COM1-9/LPT1-9
#: ——``mkdir``/``copy`` 这些名字在 Windows 上要么抛 ``OSError``、要么指向设备而不是文件。
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)])


def _is_windows_reserved(segment: str) -> bool:
    """段名是不是 Windows 保留设备名（**忽略大小写与扩展名**）。

    Windows 按**第一个点之前**的部分判设备名：``CON``/``con``/``con.md``/``NUL.txt``
    一律是设备名；``console``/``com10``/``null`` 不是。
    """
    base = segment.split(".", 1)[0].strip().upper()
    return base in _WINDOWS_RESERVED


def _safe_segment(raw: str) -> str:
    """skill 名 → **单个路径段**：去掉路径分隔符（取最后一段），拒绝 ``.`` / ``..`` /
    含 ``:`` 的名字、空串与 Windows 保留设备名。

    ``name: ../../escape`` 这类 frontmatter 名**不得**越界写出 skills 目录——落点名一律
    收敛成一个路径段。

    **含冒号的名字一律拒**（Windows 盘符/保留设备名都靠冒号）：``Path(base) / "D:evil"``
    在 Windows 上是**盘符相对路径**（pathlib 直接丢掉 base 前缀，落点是相对 D 盘 CWD 的
    路径——根本不在 skills 目录内），``"C:.."`` 落到 base 的上一层。

    **保留设备名一律拒**（``CON``/``NUL``/``COM1``…，忽略大小写与扩展名）：这类名字在
    Windows 上不是普通目录名（``mkdir`` 抛 ``OSError``，或指向设备而非文件）。
    """
    text = str(raw or "").strip().replace("\\", "/")
    segment = text.rsplit("/", 1)[-1].strip()
    if not segment or segment in (".", ".."):
        raise ValueError(f"skill 名非法（须为单个路径段，不得是 . 或 ..）：{raw!r}")
    if ":" in segment:
        raise ValueError(f"skill 名非法（不得含 Windows 盘符冒号）：{raw!r}")
    if _is_windows_reserved(segment):
        raise ValueError(f"skill 名非法（不得是 Windows 保留设备名）：{raw!r}")
    return segment


def resolve_import(src: Path) -> tuple[str, Path, bool]:
    """解析导入源 → ``(skill 名, 待复制路径, 是否整目录)``。

    - 单文件：name = frontmatter name，缺省 = 文件 stem；
    - 目录：name = 其 SKILL.md 的 frontmatter name，缺省 = 目录名；目录内无 SKILL.md → 报错。

    名字**一律约束为单个路径段**（去分隔符、拒绝 ``.`` / ``..``）——frontmatter 名是外部
    提供的，不可信（``name: ../../escape`` 会越界写出 skills 目录）。

    源不存在 / 目录内无 SKILL.md / 名字非法 → **ValueError**（调用方如实报错，不炸）。
    """
    src = Path(src)
    if not src.exists():
        raise ValueError(f"路径不存在：{src}")
    if src.is_dir():
        skill_file = src / SKILL_FILE
        if not skill_file.is_file():
            raise ValueError(f"目录中没有 {SKILL_FILE}：{src}")
        name = _safe_segment(_frontmatter_name(skill_file) or src.name)
        return name, src, True
    # 兜底名**不能**是 "SKILL"（独立审核 D3）：BOM 吃掉 frontmatter 时 frontmatter_name
    # 为空 → 落到 src.stem = "SKILL" → skill 被落成 ``skills/SKILL/SKILL.md``，
    # /skills 显示「SKILL（user）：（无描述）」。→ 用**父目录名**兜底（单文件导入时
    # 目录名才有意义），实在没有才退回 stem。
    fallback = src.parent.name if src.parent.name and src.parent.name != "skills" else src.stem
    name = _safe_segment(_frontmatter_name(src) or fallback)
    return name, src, False


def import_target(base: Path, name: str) -> Path:
    """导入落点目录：``<base>/<name>/``（base = 用户级或项目级 skills 根）。

    名字同样受**单个路径段**约束（纵深防御：落点不得越出 base）。
    """
    return Path(base) / _safe_segment(name)


def copy_skill(src_path: Path, target_dir: Path) -> Path:
    """把源 skill 复制进 ``target_dir``（文件 → ``target_dir/SKILL.md``；目录 → 整目录）。

    已存在的同名文件被覆盖——**覆盖前必须由调用方过确认卡**（本函数不弹确认）。
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if Path(src_path).is_dir():
        shutil.copytree(src_path, target_dir, dirs_exist_ok=True)
    else:
        shutil.copy2(src_path, target_dir / SKILL_FILE)
    return target_dir
