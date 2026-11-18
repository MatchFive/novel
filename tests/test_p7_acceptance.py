"""P7 集成验收：清单驱动的端到端行为。

三场景：
① **清单驱动**：项目级放一份自定义清单 → 验收提示词含该清单条目 → 模型判一条 fail →
   **一条 issue 落库**（title=条目名、detail 含 evidence）+ verdict 至少 fix +
   观测报「清单 N 条，其中未通过 1 条」；
② **同名覆盖**：用户级与项目级同名条目 → 提示词里是**项目级**那条；
③ **内置默认**：无任何自定义清单 → 提示词含内置清单里对应 stage 的条目
   （情节级 → outline；全书级 → finish）。

LLM 全 mock（按提示词内容路由），其余链路（清单读盘、编排、issues 落库）真实跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

BODY = "第一夜，风雪压塌了山门。少年握紧了手里的木剑。"
QUOTE = "少年握紧了手里的木剑"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """顺序吐脚本的假 llm（记录每次提示词）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings():
    return Settings(library_root=Path("x"), providers={}, default_provider="",
                    default_tier="standard", web_verify=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """一个有一章定稿正文的项目 + **隔离的用户级清单目录**。"""
    info = create_project(tmp_path, "清单验收书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜",
                                                "status": "done", "objective": "推进主线"},
                                   body=BODY))
    user_dir = tmp_path / "user_checklists"
    user_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("harness.checklists.user_checklist_dir",
                        lambda stage: user_dir / stage)
    return info, store, user_dir


def _payload(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _checklist_item(item="人物与关系", verdict="fail", evidence="第 3 章关系没交代"):
    return {"item": item, "verdict": verdict, "evidence": evidence}


async def _run(store, llm, info, *, kind="global"):
    from harness.check.runner import run_check

    events: list[tuple[str, dict]] = []
    text = await run_check(store=store, mem=MemoryStore(info.path), llm=llm,
                           prompts=PromptLoader(home_dir=info.path), settings=_settings(),
                           kind=kind, chapters=None, editor_mode=False,
                           emit=lambda k, p: events.append((k, p)))
    return text, events


# ------------------------------------------------------------------ 场景①

async def test_scenario_checklist_drives_issue_and_verdict(project):
    """① 项目级自定义清单 → 进提示词 → 判 fail 的条目落成 issue + verdict 至少 fix。"""
    info, store, _user_dir = project
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    (clist / "我的清单.md").write_text("本书必须交代主角的师门来历。\n", encoding="utf-8")

    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[],
                                checklist=[_checklist_item(item="我的清单",
                                                          evidence="第三章没交代师门")]))])
    text, events = await _run(store, llm, info)

    # ① 提示词里含该条目（编号 + 提示）——**内置两条在前**，自定义条目排第 3
    assert "3. 我的清单：本书必须交代主角的师门来历。" in llm.calls[0]
    assert "1. 情节检查-全书清单：" in llm.calls[0]
    # ② 判 fail 的条目落成 issue
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].source == "checklist"
    assert pending[0].title == "我的清单"
    assert "清单未通过" in pending[0].detail
    assert "第三章没交代师门" in pending[0].detail
    # ③ verdict 至少 fix（模型自称 pass 也不算 pass）
    assert "verdict=fix" in text.splitlines()[0]
    # ④ 观测报清单统计（首行之外另起一行）
    assert "清单 1 条，其中未通过 1 条" in text
    # ⑤ 事件带清单统计
    report = next(p for k, p in events if k == "check_report")
    assert report["checklist"] == 1
    assert report["checklist_failed"] == 1


async def test_scenario_checklist_rerun_does_not_accumulate(project):
    """①的续：重跑验收 → 同一条目的清单 issue **替换**而不是累积。"""
    info, store, _user_dir = project
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    (clist / "我的清单.md").write_text("本书必须交代主角的师门来历。\n", encoding="utf-8")

    payload = _payload(verdict="pass", score=9, issues=[],
                       checklist=[_checklist_item(item="我的清单")])
    for _ in range(2):
        llm = FakeLLM([_Resp(payload)])
        await _run(store, llm, info)

    issues = IssuesStore(info.path)
    assert len(issues.list("pending")) == 1      # 替换，不累积
    assert len(issues.list("resolved")) == 1


async def test_scenario_checklist_pass_clears_previous_failure(project):
    """**B-3/D3**：上轮判 fail、本轮判 **pass** → 上轮那条 issue 必须被**清掉**。

    只对「本次判 fail」的条目做 supersede，会让台账里**长期留一条已经不成立的失败**
    （带的是上一轮的 evidence）——「重跑是替换不是累积」只做了一半（独立复审 D3 实测）。
    """
    info, store, _user_dir = project
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    (clist / "我的清单.md").write_text("本书必须交代主角的师门来历。\n", encoding="utf-8")

    llm = FakeLLM([_Resp(_payload(verdict="fix", score=5, issues=[],
                                  checklist=[_checklist_item(item="我的清单",
                                                            verdict="fail",
                                                            evidence="第三章没交代师门")]))])
    await _run(store, llm, info)
    assert len(IssuesStore(info.path).list("pending")) == 1

    # 第二轮：同一条目判 pass
    llm2 = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[],
                                   checklist=[_checklist_item(item="我的清单",
                                                             verdict="pass")]))])
    await _run(store, llm2, info)

    issues = IssuesStore(info.path)
    assert issues.list("pending") == []          # 过期失败被清掉
    assert len(issues.list("resolved")) == 1


async def test_scenario_checklist_long_titles_do_not_collide(project):
    """**D5**：两条清单标题**前 40 字相同** → 不能互相顶掉。

    截断到 `_TITLE_MAX`(40) 会把「不同条目」压成「同一条目」→ 后一条判 fail 时把前一条的
    pending 置 resolved → 前一条的失败从台账里消失（独立复审 D5）。

    注意：**清单条目的标题是文件名**（正文才是判定提示）——所以要造出这个碰撞，
    得用两个**共享 45 字前缀的长文件名**。
    """
    info, store, _user_dir = project
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    shared = "标" * 45
    name_a, name_b = shared + "甲", shared + "乙"
    (clist / f"{name_a}.md").write_text("甲的要求。\n", encoding="utf-8")
    (clist / f"{name_b}.md").write_text("乙的要求。\n", encoding="utf-8")

    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[], checklist=[
        _checklist_item(item=name_a, verdict="fail", evidence="甲没过"),
        _checklist_item(item=name_b, verdict="fail", evidence="乙没过"),
    ]))])
    await _run(store, llm, info)

    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 2                    # 两条都在，互不顶掉
    assert {p.title for p in pending} == {name_a, name_b}


# ------------------------------------------------------------------ 场景②

async def test_scenario_project_checklist_overrides_user(project):
    """② 同名条目 → 提示词里是**项目级**那条（用户级被覆盖）。"""
    info, store, user_dir = project
    (user_dir / "finish").mkdir(parents=True, exist_ok=True)
    (user_dir / "finish" / "同名清单.md").write_text("用户版提示。\n", encoding="utf-8")
    clist = info.path / ".harness" / "checklists" / "finish"
    clist.mkdir(parents=True, exist_ok=True)
    (clist / "同名清单.md").write_text("项目版提示。\n", encoding="utf-8")

    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[], checklist=[]))])
    await _run(store, llm, info)

    assert "同名清单：项目版提示。" in llm.calls[0]
    assert "用户版提示" not in llm.calls[0]


# ------------------------------------------------------------------ 场景③

async def test_scenario_builtin_defaults_present(project):
    """③ 无自定义清单 → 提示词含内置清单（全书级 finish / 情节级 outline）。"""
    info, store, _user_dir = project

    llm = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[], checklist=[]))])
    await _run(store, llm, info, kind="global")
    assert "情节检查-全书清单" in llm.calls[0]
    assert "伏笔管理-记录定期检查" in llm.calls[0]

    llm2 = FakeLLM([_Resp(_payload(verdict="pass", score=9, issues=[], checklist=[]))])
    await _run(store, llm2, info, kind="plot")
    assert "爽点密度-阶段标准" in llm2.calls[0]
    assert "情节检查-每卷清单" in llm2.calls[0]
    assert "情节检查-全书清单" not in llm2.calls[0]
