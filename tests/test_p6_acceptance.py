"""P6 集成验收（T12）：三场景端到端——验收闭环 / 抽样覆盖 / editor_mode。

① **验收闭环**：建项目 + 写三章（记忆提取**真实跑**）→ 造一个 dead 角色晚出场 →
   ``global_check``（LLM mock：一条**真 quote**、一条**编造 quote**）→ issues 落 **1 条**
   （编造的被 quote 回验丢弃，观测如实报丢弃数）→ **重跑验收** → 同章 pending **不翻倍**
   （supersede：旧条转 resolved）→ ``/issues`` 处置 → resolved；
② **抽样覆盖**：30 章项目 → 验收抽样 **≤12 章**、**含最新已写章**、**含 40–70% 中段**的章；
   提示词的 samples 槽只含抽中的章（未抽中的章正文一个字都不进提示词）；
③ **editor_mode**：``editor_mode="on"`` → 提示词是 ``task.auto_editor_review`` 模板；
   **其余链路完全共用**（同样落 issues、同样回验、同样 supersede）。

**mock 纪律**（照 P5 集成验收的既有做法）：LLM 全 mock 但**按提示词内容路由**
（记忆提取 / 全书级验收 / 责编视角验收三类），其余链路**真实跑**——建项目、写章、
记忆提取、issues 台账、验收编排、``/issues`` 人工通道全是真代码。

**审批陷阱**：真 ``ApprovalUI`` 在非 TTY 下走 pending.md 文件协议会**阻塞**——
``/issues`` 一律用假审批门驱动（照 ``tests/test_cli_repl.py`` 的 ``_issue_ui`` 手法）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from harness.config import ProviderConfig, Settings
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

# ------------------------------------------------------------------ 正文 fixture

#: 第 1 章：甲死于此章（记忆提取把甲记成 dead）
BODY1 = "第一夜，风雪压塌了山门。甲握着木剑冲进山寨，倒在血泊里。"
#: 第 2 章：甲不出场
BODY2 = "第二夜，火光映红了半边天。乙抱着断桥的木桩，一直守到黎明。"
#: 第 3 章：**已死的甲又出场**（本场景的连续性破绽，验收意见就指这里）
BODY3 = "夜深，乙推开后门，看见甲提刀站在雪地里。"

#: 真 quote：逐字命中 BODY3（quote 回验通过 → 落库）
REAL_QUOTE = "看见甲提刀站在雪地里"
#: 编造 quote：正文里根本没有（quote 回验不中 → 丢弃，不落库）
FAKE_QUOTE = "甲跪在祠堂里求乙原谅"

#: 验收意见正文（title = 前 40 字；detail = 「type：意见；建议：fix」）
ISSUE_TEXT = "已故角色甲在正文里仍以活人形态出场"
ISSUE_FIX = "改为回忆或牌位"
ISSUE_TYPE = "character"

#: 观测首行（run_check 的逐字口径）
FIRST_LINE_OK = "验收完成：verdict=fix、意见 1 条（丢弃无证据 1 条），已落 issues"


# ------------------------------------------------------------------ mock LLM

class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class RoutingLLM:
    """按提示词内容路由的假 llm：routes = [(判定, 响应文本)]；未命中 → 断言失败。

    路由键（都取自模板正文里的独有措辞，不靠调用顺序猜）：
    - 「【章节正文】」→ 记忆提取（``task.memory_extract``）；
    - 「总编辑」/「签约责编」→ 两个全书级验收模板。
    """

    def __init__(self, routes, *, usage=None):
        self._routes = list(routes)
        self.calls: list[str] = []
        self.usage = usage
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192,
                       thinking=False):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        for match, response in self._routes:
            if match(prompt):
                return _Resp(response, self.usage)
        raise AssertionError(f"未路由的提示词：{prompt[:160]}")


# ------------------------------------------------------------------ 载荷 fixture

def _extraction_payload(*, title, summary, location, characters, state_characters,
                        promises=()) -> str:
    """记忆提取的五键载荷（真实提取链要的五键结构）。"""
    return json.dumps({
        "events": [{"title": title, "summary": summary, "location": location,
                    "time_marker": "深夜", "characters": characters,
                    "new_info": ["布防图"], "foreshadow": "后山密道"}],
        "character_events": [], "info_updates": [],
        "promise_updates": list(promises),
        "state_updates": {"characters": state_characters, "world": []},
    }, ensure_ascii=False)


def _check_payload(chapter: int) -> str:
    """验收输出：一条**真 quote**（该落库）+ 一条**编造 quote**（该被丢弃）。"""
    return json.dumps({
        "checklist": [{"item": "人物与关系", "verdict": "fail",
                       "evidence": "已故角色仍在正文出场"}],
        "verdict": "fix",
        "score": 6,
        "issues": [
            {"chapter": f"第{chapter}章", "type": ISSUE_TYPE, "dimension": "人物",
             "quote": REAL_QUOTE, "issue": ISSUE_TEXT, "fix": ISSUE_FIX},
            {"chapter": f"第{chapter}章", "type": ISSUE_TYPE, "dimension": "人物",
             "quote": FAKE_QUOTE, "issue": "编造的矛盾", "fix": "无"},
        ],
    }, ensure_ascii=False)


# ------------------------------------------------------------------ 环境 helper

def _settings(tmp_path=None) -> Settings:
    """带一个假 provider 的设置（LLM 全 mock，provider 只用来选模型名）。"""
    return Settings(
        library_root=Path(tmp_path) if tmp_path is not None else Path("."),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m-lite", standard="m-std", pro="m-pro")},
        default_provider="test",
        default_tier="standard",
        web_verify=False,
    )


def _prompts(tmp_path, info) -> PromptLoader:
    """项目级 + 空用户级 → 只走内置模板（不受真实用户目录里的同名模板影响）。"""
    home = Path(tmp_path) / "home"
    (home / ".harness" / "prompts").mkdir(parents=True, exist_ok=True)
    return PromptLoader(project_root=info.path, home_dir=home)


def _write_chapter(store: ProjectStore, seq: int, title: str, body: str,
                    objective: str = "推进主线") -> None:
    store.write_chapter(ChapterDoc(
        seq=seq, meta={"seq": seq, "title": title, "status": "done",
                       "objective": objective, "beats": [{"pov": "乙"}]},
        body=body))


def _seed_brief(info) -> None:
    """落一份 run brief（premise/goals/constraints 三个槽有真值，不全是占位）。"""
    from harness.run.state import RunState

    state = RunState.new("少年修士在风雪山寨里查一桩灭门案", ["outline"])
    state.brief["constraints"] = ["不写系统面板", "单线叙事"]
    state.save(info.path)


def _drive_issues_repl(info, monkeypatch, picks: list[str]) -> list[tuple[str, list[str]]]:
    """驱动 REPL 的 ``/issues`` 通道（假审批门——真 ApprovalUI 在非 TTY 下会阻塞）。

    返回每次审批的（问题, 选项），供断言。
    """
    from harness.cli import main
    from harness.run.approval import Choice

    asked: list[tuple[str, list[str]]] = []

    class _FakeUI:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, question, options, multi=False, note=None):
            asked.append((question, list(options)))
            return Choice(indices=[options.index(picks[len(asked) - 1])])

    monkeypatch.setattr("harness.session.ApprovalUI", _FakeUI)
    settings = _settings()
    monkeypatch.setattr("harness.cli.load_settings", lambda *a, **k: settings)
    monkeypatch.setattr("harness.repl.load_settings", lambda *a, **k: settings)
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    return asked


# ------------------------------------------------------------------ 场景① 验收闭环

async def test_scenario_acceptance_closed_loop(tmp_path, monkeypatch, capsys):
    """① 验收闭环：三章 + 真记忆提取 → 验收落 1 条（编造的被丢）→ 重跑不翻倍 → /issues 处置 resolved。"""
    from harness.run.events import EventLog
    from harness.tools.check import global_check
    from harness.tools.memory import extract_memory

    info = create_project(tmp_path, "验收闭环书")
    store = ProjectStore(info.path)
    mem = MemoryStore(info.path)
    prompts = _prompts(tmp_path, info)
    settings = _settings(tmp_path)
    _seed_brief(info)

    # 名册（记忆提取按名册过滤角色名——不在名册里的角色名不得进记忆）
    store.upsert_character({"name": "甲", "role_type": "主角"})
    store.upsert_character({"name": "乙", "role_type": "主角"})

    # 三章定稿：甲死在第 1 章，第 3 章里已死的甲又出场
    _write_chapter(store, 1, "夜探山寨", BODY1)
    _write_chapter(store, 2, "断桥", BODY2)
    _write_chapter(store, 3, "归墟", BODY3)

    check_json = _check_payload(3)
    llm = RoutingLLM([
        # 记忆提取：按「【章节正文】」+ 该章正文逐字路由（三章各一次真调用）
        (lambda p: "【章节正文】" in p and BODY1 in p,
         _extraction_payload(
             title="夜探山寨", summary="甲深入山寨落单", location="山寨",
             characters=["甲"], state_characters=[{"name": "甲", "status": "dead"}],
             promises=[{"kind": "foreshadow", "content": "后山密道",
                        "by_character": "甲", "status": "new", "resolution": ""}])),
        (lambda p: "【章节正文】" in p and BODY2 in p,
         _extraction_payload(
             title="断桥阻截", summary="乙守住断桥", location="断桥",
             characters=["乙"], state_characters=[{"name": "乙", "status": "alive"}])),
        (lambda p: "【章节正文】" in p and BODY3 in p,
         _extraction_payload(
             title="归墟", summary="乙在后门撞见甲", location="后门",
             characters=["乙", "甲"], state_characters=[])),
        # 验收复核（全书级）
        (lambda p: "总编辑" in p, check_json),
        (lambda p: "签约责编" in p, check_json),
    ])

    # --- 记忆提取真实跑（三章，逐章走 extract_memory 工具链）
    for seq in (1, 2, 3):
        extracted = await extract_memory(store, llm, prompts, settings, {"chapter_id": seq})
        assert extracted.text == f"extract_memory：第{seq}章——记忆提取完成"

    # 提取产物真落盘：章记忆文件 + 现状快照（甲 dead 是「dead 角色晚出场」的前置）
    for seq in (1, 2, 3):
        assert (info.path / "memory" / f"{seq:04d}.md").exists()
    state = mem.read_state()
    assert state["characters"]["甲"]["status"] == "dead"
    assert state["characters"]["乙"]["status"] == "alive"
    # 真实记忆进了验收上下文（未兑现伏笔槽来自章记忆，不是写死的）
    assert mem.read_chapter_memory(1)["promise_updates"][0]["content"] == "后山密道"

    # --- 验收：一条真 quote + 一条编造 quote
    before = {p.name: p.read_bytes() for p in (info.path / "chapters").iterdir()}
    result = await global_check(store, llm, prompts, settings, {})
    assert result.text.splitlines()[0] == FIRST_LINE_OK      # 观测如实报丢弃数

    issues = IssuesStore(info.path)
    pending = issues.list("pending")
    # 两条：① 意见（编造的那条已被回验丢弃）② 清单判 fail 的条目（P7 清单驱动落 issues）
    assert len(pending) == 2
    checklist_issue = next(i for i in pending if i.source == "checklist")
    assert checklist_issue.title == "人物与关系"
    assert "清单未通过" in checklist_issue.detail
    landed = next(i for i in pending if i.quote == REAL_QUOTE)
    assert landed.source == "check"
    assert landed.chapter == 3
    assert landed.quote == REAL_QUOTE
    assert landed.title == ISSUE_TEXT
    assert landed.detail == f"{ISSUE_TYPE}：{ISSUE_TEXT}；建议：{ISSUE_FIX}"
    assert all(i.quote != FAKE_QUOTE for i in issues.list(None))
    # 只报不改：验收不碰章正文（逐字节未动）
    after = {p.name: p.read_bytes() for p in (info.path / "chapters").iterdir()}
    assert before == after
    # 事件链：check_report 落进 events.log
    reports = [e for e in EventLog(info.path).tail(100) if e["kind"] == "check_report"]
    assert len(reports) == 1
    assert reports[0]["payload"] == {"verdict": "fix", "score": 6, "issues": 1,
                                     "discarded": 1, "kind": "global",
                                     "checklist": 1, "checklist_failed": 1}
    # 真记忆进了提示词（未兑现伏笔槽）
    assert "后山密道" in llm.calls[-1]

    # --- 重跑验收：同章 pending 不翻倍（旧条 supersede 转 resolved）
    again = await global_check(store, llm, prompts, settings, {})
    assert again.text.splitlines()[0] == FIRST_LINE_OK
    pending = issues.list("pending")
    resolved = issues.list("resolved")
    # 意见按章 supersede、清单条目按标题 supersede → 重跑后各留 1 条（旧条转 resolved）
    assert len(pending) == 2
    assert len(resolved) == 2
    opinion_pending = next(i for i in pending if i.source == "check")
    opinion_resolved = next(i for i in resolved if i.source == "check")
    assert opinion_pending.id != opinion_resolved.id
    assert opinion_pending.chapter == 3 and opinion_resolved.chapter == 3

    # --- /issues 人工终裁 → resolved
    # 两条待处置：意见 + 清单 fail 条目（P7）→ 两次审批都选「已解决」
    asked = _drive_issues_repl(info, monkeypatch, ["已解决", "已解决"])
    out = capsys.readouterr().out
    assert asked, "应至少问过一次处置"
    assert "【验收】" in out
    assert "→ 已标记 resolved" in out
    assert issues.list("pending") == []
    assert any(i.id == pending[0].id for i in issues.list("resolved"))


# ------------------------------------------------------------------ 场景② 抽样覆盖

async def test_scenario_sampling_coverage(tmp_path):
    """② 抽样覆盖：30 章 → 抽样 ≤12 章、含最新章、含 40–70% 中段；samples 槽只含抽中的章。"""
    from harness.tools.check import global_check

    info = create_project(tmp_path, "抽样覆盖书")
    store = ProjectStore(info.path)
    prompts = _prompts(tmp_path, info)
    settings = _settings(tmp_path)

    total = 30
    bodies: dict[int, str] = {}
    for seq in range(1, total + 1):
        body = f"第{seq}章的正文：风雪压塌了山门第{seq}夜，少年握着第{seq}柄木剑。"
        bodies[seq] = body
        _write_chapter(store, seq, f"第{seq}夜", body)

    llm = RoutingLLM([
        (lambda p: "总编辑" in p,
         json.dumps({"verdict": "pass", "issues": []}, ensure_ascii=False)),
    ])
    result = await global_check(store, llm, prompts, settings, {})
    assert "验收完成" in result.text

    prompt = llm.calls[-1]
    sampled = [int(m) for m in re.findall(r"【第(\d+)章", prompt)]
    assert 0 < len(sampled) <= 12                              # 抽样章数有上限
    assert total in sampled                                     # 最新已写章必抽
    mid = [s for s in sampled if int(total * 0.4) <= s - 1 < int(total * 0.7)]
    assert mid, f"抽样里没有 40–70% 中段的章：{sampled}"        # ConStory-Bench 中段加权
    assert sampled == sorted(sampled)
    # samples 槽只含抽中的章：未抽中的章正文一个字都不进提示词
    for seq, body in bodies.items():
        if seq in sampled:
            assert body in prompt
        else:
            assert body not in prompt


# ------------------------------------------------------------------ 场景③ editor_mode

async def test_scenario_editor_mode_shares_chain(tmp_path):
    """③ editor_mode="on" → 责编视角模板；其余链路完全共用（落 issues / 回验 / supersede）。"""
    from harness.tools.check import global_check

    info = create_project(tmp_path, "责编视角书")
    store = ProjectStore(info.path)
    prompts = _prompts(tmp_path, info)
    settings = _settings(tmp_path)

    _write_chapter(store, 1, "断桥", BODY2)
    _write_chapter(store, 2, "归墟", BODY3)     # 已死的甲又出场

    check_json = _check_payload(2)
    llm = RoutingLLM([
        (lambda p: "签约责编" in p, check_json),
        (lambda p: "总编辑" in p, check_json),
    ])

    result = await global_check(store, llm, prompts, settings, {"editor_mode": "on"})
    prompt = llm.calls[-1]
    assert "签约责编" in prompt                               # 走 task.auto_editor_review
    assert "总编辑" not in prompt

    # 同样落 issues + 同样回验（编造的那条照丢，观测照报丢弃数）
    assert result.text.splitlines()[0] == FIRST_LINE_OK
    issues = IssuesStore(info.path)
    editor_issue = next(i for i in issues.list("pending") if i.quote == REAL_QUOTE)
    assert len(issues.list("pending")) == 2      # 意见 + 清单 fail 条目（P7）
    assert editor_issue.source == "check"
    assert editor_issue.chapter == 2
    assert editor_issue.quote == REAL_QUOTE
    assert editor_issue.title == ISSUE_TEXT
    assert editor_issue.detail == f"{ISSUE_TYPE}：{ISSUE_TEXT}；建议：{ISSUE_FIX}"

    # 同样 supersede：重跑后同章 pending 仍 1 条，旧条转 resolved
    await global_check(store, llm, prompts, settings, {"editor_mode": "on"})
    pending = issues.list("pending")
    resolved = issues.list("resolved")
    # 意见按章 supersede、清单条目按标题 supersede → 重跑后各留 1 条在 pending，
    # 旧的各转 resolved（**不累积**——这是 P7 清单驱动的关键口径）
    assert len(pending) == 2                  # 意见 + 清单 fail 条目（P7）
    assert len(resolved) == 2
    assert pending[0].id != resolved[0].id

    # 「其余链路完全共用」：同一份意见走总编辑视角，落库口径逐字相同
    other = create_project(tmp_path, "总编辑视角书")
    other_store = ProjectStore(other.path)
    other_prompts = _prompts(tmp_path, other)
    _write_chapter(other_store, 1, "断桥", BODY2)
    _write_chapter(other_store, 2, "归墟", BODY3)
    await global_check(other_store, llm, other_prompts, settings, {})
    global_issue = IssuesStore(other.path).list("pending")[0]
    assert (global_issue.source, global_issue.chapter, global_issue.quote,
            global_issue.title, global_issue.detail) == \
           (editor_issue.source, editor_issue.chapter, editor_issue.quote,
            editor_issue.title, editor_issue.detail)
