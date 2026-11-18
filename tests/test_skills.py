"""Skills 机制（P5 Task 6）：两级扫描器 + 定向注入映射 + list_skills/read_skill 只读工具。

口径钉死：
- 两级作用域：用户级 ``<user_dir>/*/SKILL.md`` + 项目级 ``<root>/.harness/skills/*/SKILL.md``；
- **项目级覆盖用户级同名**；
- 坏 frontmatter → 跳过该 skill（不炸），其余照常；
- 无 frontmatter → name 取目录名、description 取空串；
- ``Skill.body`` = SKILL.md 全文（含 frontmatter）；
- ``skill_injection``：无映射 / 无 skill → ``""``（字节级零变化）；
- ``load_skill(scopes=("project",))`` → 用户级 skill 不可见（文风只认项目级）。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.whitelist import TOOLS_ALL, allowed_for_run
from harness.skills import (
    TARGETED_INJECT,
    list_skills,
    load_skill,
    read_skill,
    scan_skills,
    skill_injection,
)
from harness.store.project import ProjectStore
from harness.tools import readonly_registry

BODY = "要点一：先定目标，再拆节拍。"


def write_skill(base: Path, dirname: str, *, name: str | None = None,
                description: str | None = None, body: str = BODY,
                frontmatter: bool = True) -> Path:
    """造一个 ``<base>/<dirname>/SKILL.md``；frontmatter=False → 无 frontmatter 的纯正文。"""
    d = base / dirname
    d.mkdir(parents=True, exist_ok=True)
    text = ""
    if frontmatter:
        text = "---\n"
        if name is not None:
            text += f"name: {name}\n"
        if description is not None:
            text += f"description: {description}\n"
        text += "---\n\n"
    text += body + "\n"
    path = d / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "技能书")
    return ProjectStore(info.path)


@pytest.fixture
def user_dir(tmp_path):
    d = tmp_path / "user_skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(autouse=True)
def default_user_dir(tmp_path, monkeypatch):
    """隔离真实 ``~/.harness/skills``。

    工具层（list_skills/read_skill）走默认 ``user_skills_dir()``，测试若不隔离就会
    读到开发机上真实的用户 skill——既不确定（空库断言可能假失败），也不该让测试
    触碰真实用户目录。``scan_skills`` 的 ``user_dir`` 参数化路径不受影响。
    """
    d = tmp_path / "home_skills"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: d)
    return d


def project_skills_dir(store: ProjectStore) -> Path:
    return store.root / ".harness" / "skills"


# ------------------------------------------------------------------ 扫描

def test_scan_two_scopes(store, user_dir):
    """两级都在列，scope 正确。"""
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")
    write_skill(user_dir, "outline_gold", name="outline_gold", description="情节黄金准则")

    found = scan_skills(store.root, user_dir)

    assert set(found) == {"master_plan", "outline_gold"}
    assert found["master_plan"].scope == "project"
    assert found["outline_gold"].scope == "user"
    assert found["master_plan"].description == "卷骨架方法论"
    assert BODY in found["master_plan"].body


def test_project_overrides_user(store, user_dir):
    """同名两级都在 → 项目级赢。"""
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="项目版")
    write_skill(user_dir, "master_plan", name="master_plan", description="用户版")

    found = scan_skills(store.root, user_dir)

    assert found["master_plan"].scope == "project"
    assert found["master_plan"].description == "项目版"


def test_bad_frontmatter_skipped(store, user_dir):
    """坏 YAML → 跳过该 skill，不炸；其余 skill 正常返回。"""
    bad = project_skills_dir(store) / "broken"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "SKILL.md").write_text("---\nname: [未闭合\n---\n正文\n", encoding="utf-8")
    write_skill(project_skills_dir(store), "good", name="good", description="好的")

    found = scan_skills(store.root, user_dir)

    assert "broken" not in found
    assert found["good"].description == "好的"


def test_no_frontmatter_name_from_dir(store, user_dir):
    """无 frontmatter → name 取目录名、description 空串。"""
    write_skill(project_skills_dir(store), "style", frontmatter=False)

    found = scan_skills(store.root, user_dir)

    assert "style" in found
    assert found["style"].description == ""
    assert BODY in found["style"].body


def test_scan_empty(store, user_dir):
    """两级皆空 → 空 dict（不炸）。"""
    assert scan_skills(store.root, user_dir) == {}


def test_load_skill_scopes_project_only(store, user_dir):
    """scopes=("project",) → 用户级 skill 不可见（文风只认项目级）。"""
    write_skill(user_dir, "style", name="style", description="用户文风")
    write_skill(project_skills_dir(store), "style", name="style", description="项目文风")

    assert load_skill(store.root, "style", user_dir).scope == "project"
    assert load_skill(store.root, "style", user_dir, scopes=("user",)) is None

    write_skill(user_dir, "only_user", name="only_user")
    assert load_skill(store.root, "only_user", user_dir, scopes=("project",)) is None
    assert load_skill(store.root, "only_user", user_dir) is not None


def test_load_skill_missing(store, user_dir):
    """找不到 → None。"""
    assert load_skill(store.root, "nope", user_dir) is None
    assert load_skill(store.root, "", user_dir) is None


def test_load_skill_falls_back_to_directory_name(store, user_dir):
    """M-12：frontmatter name 查不到 → 再按**目录名**找一次（不得静默失效）。"""
    write_skill(project_skills_dir(store), "style", name="我的文风", description="项目文风")

    skill = load_skill(store.root, "style", user_dir)

    assert skill is not None
    assert skill.scope == "project"
    assert BODY in skill.body


def test_load_skill_scope_mismatch_falls_back_to_directory(store, user_dir):
    """name 命中但 **scope 不符** → 继续走目录名兜底（不得直接 None）。

    布局：用户级有个 frontmatter ``name=style`` 的 skill + 项目级**目录** ``style``
    （frontmatter name 不同）。``scopes=("project",)`` 时用户级那个不可见 → 必须
    兜底到项目级那个，而不是返回 None（静默失效）。
    """
    write_skill(user_dir, "user_style", name="style", description="用户文风")
    write_skill(project_skills_dir(store), "style", name="我的文风", description="项目文风")

    skill = load_skill(store.root, "style", user_dir, scopes=("project",))

    assert skill is not None
    assert skill.scope == "project"
    assert skill.description == "项目文风"


def test_bad_frontmatter_skill_leaves_no_backup(store, user_dir):
    """坏 frontmatter 的 skill 被跳过后，**不产生任何 *.bak**（T6 的「不写 .bak 副作用」条款）。"""
    bad = project_skills_dir(store) / "broken"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "SKILL.md").write_text("---\nname: [未闭合\n---\n正文\n", encoding="utf-8")
    write_skill(user_dir, "broken_user", name="broken_user")

    found = scan_skills(store.root, user_dir)

    assert "broken" not in found
    assert load_skill(store.root, "broken", user_dir) is None
    assert skill_injection(store.root, "task.auto_master_plan", user_dir) == ""
    assert list(store.root.rglob("*.bak")) == []
    assert list(user_dir.rglob("*.bak")) == []


# ------------------------------------------------------------------ 注入不遍历目录（M-9）

def _no_scan(monkeypatch):
    """把 scan_skills 换成会炸的桩：注入路径与 style 钩子**不得遍历目录**。"""
    from harness import skills

    def _boom(*args, **kwargs):
        raise AssertionError("skills 注入路径不得遍历 skills 目录")

    monkeypatch.setattr(skills, "scan_skills", _boom)


def test_skill_injection_does_not_scan_directory(store, user_dir, monkeypatch):
    """定向注入按名字直读那一个 SKILL.md（每拍重扫全部 SKILL.md 违反 P4 I-1 纪律）。"""
    _no_scan(monkeypatch)
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")

    out = skill_injection(store.root, "task.auto_master_plan", user_dir)

    assert out.startswith("\n\n【技能：master_plan】")
    assert BODY in out


def test_style_hook_does_not_scan_directory(store, user_dir, monkeypatch):
    """style 钩子同样直读那一个文件（build_draft_context 每拍调一次，不得遍历目录）。"""
    from harness.writing.context import build_draft_context

    _no_scan(monkeypatch)
    write_skill(project_skills_dir(store), "style", name="style", description="项目文风",
                body="句式短促。")

    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                              _selected(), recent_draft="")

    assert "句式短促。" in ctx.user


def test_style_skill_found_by_directory_name(store, user_dir):
    """M-12 端到端：目录名是 style 但 frontmatter 写了别的名字 → 文风仍进正文。"""
    from harness.writing.context import build_draft_context

    write_skill(project_skills_dir(store), "style", name="我的文风", description="项目文风",
                body="句式短促，少用长修饰。")

    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                              _selected(), recent_draft="")

    assert "句式短促，少用长修饰。" in ctx.user


# ------------------------------------------------------------------ 导入名单段约束（M-11）

def test_import_name_constrained_to_single_segment(tmp_path):
    """frontmatter name 含路径分隔符 → 目标名收敛为**单个路径段**（不得越界写出 skills 目录）。"""
    from harness.skills import import_target, resolve_import

    src = tmp_path / "evil.md"
    src.write_text("---\nname: ../../escape\n---\n\n正文\n", encoding="utf-8")

    name, src_path, is_dir = resolve_import(src)

    assert name == "escape"
    assert "/" not in name and "\\" not in name
    assert not is_dir and src_path == src
    assert import_target(tmp_path / "skills", name) == tmp_path / "skills" / "escape"


def test_import_name_rejects_dot_segments(tmp_path):
    """frontmatter name 是 '.' / '..' → 报错（一个文件都不写）。"""
    from harness.skills import resolve_import

    for bad in (".", ".."):
        src = tmp_path / f"bad{bad}.md"
        src.write_text(f"---\nname: {bad}\n---\n\n正文\n", encoding="utf-8")
        with pytest.raises(ValueError):
            resolve_import(src)


def test_import_dir_name_constrained(tmp_path):
    """目录导入：目录名兜底同样受约束（frontmatter name 缺省时取目录名）。"""
    from harness.skills import resolve_import

    src = tmp_path / "外部目录"
    (src / "skills").mkdir(parents=True)
    (src / "skills" / "SKILL.md").write_text("---\ndescription: 无 name\n---\n\n正文\n",
                                             encoding="utf-8")

    name, _src_path, is_dir = resolve_import(src / "skills")

    assert name == "skills"
    assert is_dir is True


@pytest.mark.parametrize("raw", ["D:evil", "C:.."])
def test_import_name_rejects_windows_drive_prefix(tmp_path, raw):
    """M-11 残余：含 **Windows 盘符** 的名字（D:evil / C:..）→ 导入被拒。

    ``Path(base) / "D:evil"`` 在 Windows 上是**盘符相对路径**（pathlib 直接把 base
    前缀丢掉，落点是相对 D 盘 CWD 的路径）——根本不在 skills 目录内；
    ``C:..`` 落到 base 的上一层。两个都必须拒（盘符/保留设备名都靠冒号）。
    """
    from harness.skills import import_target, resolve_import

    src = tmp_path / "evil.md"
    src.write_text(f"---\nname: {raw}\n---\n\n正文\n", encoding="utf-8")

    with pytest.raises(ValueError):
        resolve_import(src)
    with pytest.raises(ValueError):
        import_target(tmp_path / "skills", raw)
    # 一个文件都不许落在 skills 目录之外
    skills_dir = tmp_path / "skills"
    assert not skills_dir.exists() or list(skills_dir.iterdir()) == []


# ------------------------------------------------------------------ 定向注入

def test_skill_injection_present(store, user_dir):
    """有映射且 skill 存在 → 注入块含【技能：name】与正文。"""
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")

    out = skill_injection(store.root, "task.auto_master_plan", user_dir)

    assert out.startswith("\n\n【技能：master_plan】")
    assert BODY in out


def test_skill_injection_absent(store, user_dir):
    """无 skill → 空串（调用点字节级零变化）。"""
    assert skill_injection(store.root, "task.auto_master_plan", user_dir) == ""


def test_skill_injection_no_mapping(store, user_dir):
    """无映射 → 空串（未来新任务 key 默认不注入）。"""
    write_skill(project_skills_dir(store), "whatever", name="whatever")
    assert skill_injection(store.root, "task.not_mapped", user_dir) == ""


def test_skill_injection_found_by_frontmatter_name(store, user_dir):
    """目录名 ≠ frontmatter name → 注入块**必须出现**（按 frontmatter name 兜底）。

    目录名即身份（M-12）之外，frontmatter name 也必须能找到——否则「注入静默消失」
    （与 M-12 要消灭的是同一类问题，只是搬到了注入路径上）。
    """
    write_skill(project_skills_dir(store), "my_master_plan", name="master_plan",
                description="卷骨架方法论", body="骨架要点。")

    out = skill_injection(store.root, "task.auto_master_plan", user_dir)

    assert "【技能：" in out
    assert "骨架要点。" in out


def test_skill_injection_prefers_directory_name(store, user_dir):
    """两种命名都要测到：目录名命中 → 注入的是**目录名**那个（目录名优先）。

    同一 key 在用户级还有一个 frontmatter 同名的 skill → 不得被它顶掉
    （目录名优先 = 「目录即身份」）。
    """
    write_skill(project_skills_dir(store), "master_plan", name="我的大纲",
                description="项目骨架", body="项目骨架要点。")
    write_skill(user_dir, "user_master_plan", name="master_plan",
                description="用户骨架", body="用户骨架要点。")

    out = skill_injection(store.root, "task.auto_master_plan", user_dir)

    assert "项目骨架要点。" in out
    assert "用户骨架要点。" not in out


def test_targeted_inject_mapping_three_keys():
    """裁定 17：映射固定三键（本地实际存在的三个 render 点）。"""
    assert TARGETED_INJECT == {
        "task.auto_master_plan": "master_plan",
        "task.auto_plan_plots": "plan_plots",
        "task.auto_modify_propose": "world_building",
    }


# ------------------------------------------------------------------ 只读工具

def test_list_skills_empty(store, user_dir):
    """空库 → 如实「无可用 skill」。"""
    assert list_skills(store, {}) == "无可用 skill"


def test_list_skills_two_scopes(store, default_user_dir):
    """逐行列 name（scope）：description（用户级走默认 user_skills_dir()）。"""
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")
    write_skill(default_user_dir, "gold", name="gold", description="情节准则")

    out = list_skills(store, {})

    assert "master_plan（project）：卷骨架方法论" in out
    assert "gold（user）：情节准则" in out


def test_read_skill_found(store, user_dir):
    """读全文（含 frontmatter）。"""
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")

    out = read_skill(store, {"name": "master_plan"})

    assert BODY in out
    assert "name: master_plan" in out


def test_read_skill_missing(store, user_dir):
    """未找到 → 如实「无此 skill：<name>」。"""
    assert read_skill(store, {"name": "nope"}) == "无此 skill：nope"
    assert "args.name" in read_skill(store, {})


def test_read_skill_does_not_scan_directory(store, user_dir, monkeypatch):
    """read_skill 先按**目录名直读**（不遍历目录），name 命中兜底。

    目录名命中时不得触发 ``scan_skills``（目录遍历）。
    """
    _no_scan(monkeypatch)
    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")

    out = read_skill(store, {"name": "master_plan"})

    assert BODY in out
    assert "name: master_plan" in out


def test_tools_registered_readonly(store):
    """两个工具进 registry 且 read_only=True（可并行批）。"""
    registry = readonly_registry(store)

    assert "list_skills" in registry.names
    assert "read_skill" in registry.names
    assert registry.is_read_only("list_skills") is True
    assert registry.is_read_only("read_skill") is True


def test_whitelist_updated():
    """白名单：TOOLS_ALL + 只读集含两工具；world/draft/chapters/discuss 均可用（只读）。"""
    assert "list_skills" in TOOLS_ALL
    assert "read_skill" in TOOLS_ALL
    for deliverables in (["world"], ["draft"], ["chapters"], ["outline"], ["discuss"]):
        allowed = allowed_for_run(deliverables)
        assert "list_skills" in allowed
        assert "read_skill" in allowed


# ================================================================ 三挂点定向注入（T7）

def make_settings(pro="pro-model"):
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro=pro)},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def make_llm():
    """responses：str content；记录每次请求 body 供断言（提示词即 body['messages'][0]['content']）。"""
    def factory(responses):
        calls = []

        def handler(req):
            body = json.loads(req.content)
            calls.append(body)
            item = responses[len(calls) - 1]
            if isinstance(item, str):
                item = {"content": item, "finish": "stop"}
            return httpx.Response(200, json={
                "choices": [{"message": {"content": item["content"]}, "finish_reason": item["finish"]}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10},
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
        client = OpenAICompatClient(
            ProviderConfig(base_url="https://api.test", api_key="k",
                           lite="m", standard="m", pro="m"),
            http_client=http)
        return client, calls

    return factory


def _arcs_payload():
    return json.dumps({"planned_volume_count": 1, "arcs": [
        {"vol": 1, "title": "崛起", "goal": "g", "summary": "s", "conflict": "c",
         "resolution": "r", "capacity_hint": "约40章"}]}, ensure_ascii=False)


def _plots_payload():
    return json.dumps({"plots": [{"title": "开端", "summary": "概要", "conflict": "冲突",
                                  "resolution": "收束", "beats": ["起"]}],
                       "characters": [], "need_web": []}, ensure_ascii=False)


def _edits_payload():
    return json.dumps([{"path": "world.md", "content": "---\nentries: []\n---\n\n"}],
                      ensure_ascii=False)


def test_targeted_inject_master_plan(store, make_llm):
    """挂点一：auto_master_plan（plan_arcs）→ 提示词含【技能：master_plan】与正文。"""
    from harness.tools.outline import register_outline_tools
    from harness.tools.registry import ToolRegistry

    write_skill(project_skills_dir(store), "master_plan", name="master_plan",
                description="卷骨架方法论")
    llm, calls = make_llm([_arcs_payload()])
    reg = ToolRegistry(store)
    register_outline_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    reg.execute("plan_arcs", {"instruction": "规划一卷骨架"}, allowed=frozenset({"plan_arcs"}))

    prompt = calls[0]["messages"][0]["content"]
    assert "【技能：master_plan】" in prompt
    assert BODY in prompt


def test_targeted_inject_plan_plots(store, make_llm):
    """挂点二：auto_plan_plots（master_plan）→ 提示词含【技能：plan_plots】与正文。"""
    from harness.tools.plots import master_plan

    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    write_skill(project_skills_dir(store), "plan_plots", name="plan_plots",
                description="情节规划方法论")
    llm, calls = make_llm([_plots_payload()])

    import asyncio
    asyncio.run(master_plan(store, llm, PromptLoader(home_dir=store.root),
                             make_settings(), lambda kind, payload: None, {}))

    prompt = calls[0]["messages"][0]["content"]
    assert "【技能：plan_plots】" in prompt
    assert BODY in prompt


def test_targeted_inject_modify_propose(store, make_llm):
    """挂点三：auto_modify_propose（edit_by_command）→ 提示词含【技能：world_building】。"""
    from harness.run.approval import ApprovalUI, Choice
    from harness.tools.modify import edit_by_command

    write_skill(project_skills_dir(store), "world_building", name="world_building",
                description="世界观构建方法论")
    llm, calls = make_llm([_edits_payload()])
    ui = ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root), ui,
                                {"instruction": "新增一条地理设定"}))

    prompt = calls[0]["messages"][0]["content"]
    assert "【技能：world_building】" in prompt
    assert BODY in prompt


def test_targeted_inject_absent_unchanged(store, make_llm):
    """无 skill → 提示词**字节级零变化**（注入返回空串）。"""
    from harness.tools.outline import register_outline_tools
    from harness.tools.registry import ToolRegistry

    llm, calls = make_llm([_arcs_payload()])
    reg = ToolRegistry(store)
    register_outline_tools(reg, store=store, llm=llm,
                           prompts=PromptLoader(home_dir=store.root), settings=make_settings())
    reg.execute("plan_arcs", {"instruction": "规划一卷骨架"}, allowed=frozenset({"plan_arcs"}))

    prompt = calls[0]["messages"][0]["content"]
    assert "【技能：" not in prompt
    assert "【技能" not in prompt


# ================================================================ style 文风注入（T7）

def _style_skill(store, content="句式短促，少用长修饰。"):
    write_skill(project_skills_dir(store), "style", name="style",
                description="项目文风", body=content)
    return content


def test_style_skill_in_draft_context(store):
    """项目级 style skill → build_draft_context 的 style_guide 槽有内容。"""
    from harness.writing.context import build_draft_context

    _style_skill(store)
    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                               _selected(), recent_draft="")
    assert "句式短促，少用长修饰。" in ctx.user


def test_style_absent_draft_context_unchanged(store):
    """无 style skill → style_guide 为空串（模板区块头被清）。"""
    from harness.writing.context import build_draft_context

    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                               _selected(), recent_draft="")
    assert "【风格指南】" not in ctx.user


def test_style_user_scope_not_injected(store, default_user_dir):
    """用户级 style **不注入写作**（设计 §2.6 明文：文风只认项目级）。"""
    from harness.writing.context import build_draft_context

    write_skill(default_user_dir, "style", name="style", description="用户文风",
                body="用户级文风内容")
    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                               _selected(), recent_draft="")
    assert "用户级文风内容" not in ctx.user


def test_style_edit_takes_effect(store):
    """手改 SKILL.md → 下次构建反映新值（无缓存陈旧）。"""
    from harness.writing.context import build_draft_context

    _style_skill(store)
    ctx = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                               _selected(), recent_draft="")
    assert "句式短促，少用长修饰。" in ctx.user

    write_skill(project_skills_dir(store), "style", name="style",
                description="项目文风", body="改后的文风：多用短句。")
    ctx2 = build_draft_context(store, _chapter_doc(), _plot(), _beats(), "",
                                _selected(), recent_draft="")
    assert "改后的文风：多用短句。" in ctx2.user
    assert "句式短促，少用长修饰。" not in ctx2.user


def test_style_in_deaify_vars():
    """去味两阶段的 style_guide 槽：传则填、不传则空串（向后兼容）。"""
    from harness.writing.context import build_deaify_edits_vars, build_deaify_vars

    assert build_deaify_vars([], "正文")["style_guide"] == ""
    assert build_deaify_vars([], "正文", style_guide="文风")["style_guide"] == "文风"
    assert build_deaify_edits_vars("目标")["style_guide"] == ""
    assert build_deaify_edits_vars("目标", style_guide="文风")["style_guide"] == "文风"
    assert build_deaify_vars([], "正文")["style_sample"] == ""


def _chapter_doc():
    from harness.store.project import ChapterDoc

    return ChapterDoc(seq=1, meta={"seq": 1, "title": "第1章", "status": "done",
                                   "objective": "上山拜师"}, body="")


def _plot():
    return {"summary": "山村少年入宗门", "seq": 1, "title": "开端"}


def _beats():
    return [{"pov": "甲", "location": "山门", "event": "甲上山拜师", "weight": 1}]


def _selected():
    return {"character_names": ["甲"], "world_titles": ["青石宗"]}

