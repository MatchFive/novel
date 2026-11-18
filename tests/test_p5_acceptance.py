"""P5 集成验收（T11）：四场景端到端。

① **审计闭环**：建项目 → 写两章（记忆提取真实跑）→ 造 dead 角色晚出场 + 记忆数值回退
   → /audit（LLM mock 一条真 quote 一条假 quote）→ issues 落 2 条确定性 + 1 条 LLM
   （假 quote 被丢）→ /issues 修 LLM 那条（mock fix LLM + 确认卡）→ resolved +
   记忆重提取生效；
② **联网回绝**：无 [search] 项目 web_search 明确回绝、零 HTTP；
③ **style 端到端**：/skill import 导入 style（--project）→ build_draft_context 含文风
   → 手改 SKILL.md → 下次构建新值生效；
④ **supersede**：/audit 连跑两次同章 pending 不翻倍。

LLM 全 mock（按提示词内容路由：审计复核 / 修复 / 记忆提取三类），其余链路真实跑。
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.config import ProviderConfig, Settings
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalUI, Choice
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.web import web_search


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class RoutingLLM:
    """按提示词内容路由的假 llm：routes = [(判定, 响应文本)]；未命中 → 断言失败。"""

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


def make_settings(*, search=None, web_verify=True):
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m-lite", standard="m-std", pro="m-pro")},
        default_provider="test",
        default_tier="standard",
        web_verify=web_verify,
        search=search,
    )


def extraction_payload(title="夜探山寨"):
    """五键提取 fixture。"""
    return json.dumps({
        "events": [{"title": title, "summary": "甲潜入山寨", "location": "山寨",
                    "time_marker": "深夜", "characters": [],
                    "new_info": ["布防图"], "foreshadow": "后山密道"}],
        "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [], "world": []},
    }, ensure_ascii=False)


# ------------------------------------------------------------------ 场景① 审计闭环

async def test_scenario_audit_closed_loop(tmp_path):
    """① 审计闭环：/audit 落候选（假 quote 被丢）→ /issues 交给 AI 修 → resolved + 记忆重提取。"""
    from harness.audit.fix import fix_issue
    from harness.audit.runner import run_audit

    info = create_project(tmp_path, "闭环书")
    store = ProjectStore(info.path)
    mem = MemoryStore(info.path)
    # 两章定稿：第 1 章甲退场，第 2 章正文仍含甲（R2 命中）
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一夜", "status": "done",
                                                "beats": [{"pov": "乙"}]},
                                   body="乙走进山门。甲倒在血泊里。"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "第二夜", "status": "done",
                                                "beats": [{"pov": "乙"}]},
                                   body="乙走进山门。甲的牌位立在堂上。"))
    mem.apply_state_updates({"characters": [{"name": "甲", "status": "dead"}]}, seq=1)

    real_quote = "甲的牌位立在堂上"
    fake_quote = "正文里根本不存在的片段"
    audit_items = json.dumps([
        {"chapter": 2, "quote": real_quote, "type": "character",
         "issue": "已故角色仍在正文出现", "suggestion": "改为牌位"},
        {"chapter": 2, "quote": fake_quote, "type": "character",
         "issue": "编造的矛盾", "suggestion": "无"},
    ], ensure_ascii=False)

    llm = RoutingLLM([
        (lambda p: "【待审正文】" in p, audit_items),
        (lambda p: "【章节正文】" in p, extraction_payload()),
        (lambda p: "【待替换原文】" in p, json.dumps({"replacement": "乙的牌位立在堂上"},
                                                    ensure_ascii=False)),
    ])
    prompts = PromptLoader(project_root=info.path)

    # --- /audit：确定性 1 条 + LLM 1 条（假 quote 被丢）
    text = await run_audit(store=store, mem=mem, llm=llm, prompts=prompts,
                           settings=make_settings(), chapters=[2], emit=lambda k, p: None)
    issues = IssuesStore(info.path)
    pending = issues.list("pending")
    assert len(pending) == 2, text                      # 确定性 1 + LLM 1
    assert any(i.quote == real_quote for i in pending)
    assert all(i.quote != fake_quote for i in pending)    # 假 quote 被丢
    assert "LLM 复核 1 条" in text
    # 假 quote 在**复核层**（locate_quote 回验）就被丢弃，未进落库门 → 落库门丢弃计数为 0

    # --- /issues「交给 AI 修」：mock fix LLM + 确认卡
    llm_issue = next(i for i in pending if i.quote == real_quote)
    ui = ApprovalUI(ask=lambda q, o, m=False, note=None: Choice(indices=[0]))
    fixed = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts,
                            settings=make_settings(), issue=llm_issue, ui=ui)

    assert fixed.startswith("已修改第2章")
    assert "乙的牌位立在堂上" in store.read_chapter(2).body
    assert llm_issue.id not in [i.id for i in issues.list("pending")]
    assert any(i.id == llm_issue.id for i in issues.list("resolved"))
    # 记忆重提取生效：第 2 章记忆重新落盘
    assert (info.path / "memory" / "0002.md").exists()


# ------------------------------------------------------------------ 场景② 联网回绝

async def test_scenario_web_refuses_without_config(tmp_path):
    """② 无 [search] → web_search 明确回绝、零 HTTP（服务对象根本不构造）。"""
    import harness.tools.web as web_tool

    info = create_project(tmp_path, "回绝书")
    store = ProjectStore(info.path)
    constructed = []

    class _MustNotBuild:
        def __init__(self, *args, **kwargs):
            constructed.append(1)

    original = web_tool.WebSearchService
    web_tool.WebSearchService = _MustNotBuild
    try:
        result = await web_search(store, None, PromptLoader(project_root=info.path),
                                  make_settings(), {"query": "率土之滨 内政"})
    finally:
        web_tool.WebSearchService = original

    assert "拒绝执行" in result.text
    assert "不得凭记忆编造" in result.text
    assert constructed == []          # 零 HTTP 零 LLM


# ------------------------------------------------------------------ 场景③ style 端到端

def test_scenario_style_end_to_end(tmp_path, monkeypatch, capsys):
    """③ /skill import（--project）→ build_draft_context 含文风 → 手改 SKILL.md → 新值生效。"""
    from harness.cli import main
    from harness.writing.context import build_draft_context

    info = create_project(tmp_path, "文风书")
    external = tmp_path / "外部文风"
    external.mkdir()
    (external / "SKILL.md").write_text(
        "---\nname: style\ndescription: 项目文风\n---\n\n句式短促，少用长修饰。\n",
        encoding="utf-8")

    inputs = iter([f"/skill import {external} --project", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert (info.path / ".harness" / "skills" / "style" / "SKILL.md").exists()

    store = ProjectStore(info.path)
    doc = ChapterDoc(seq=1, meta={"seq": 1, "title": "第1章", "status": "done",
                                  "objective": "上山拜师", "beats": [{"pov": "甲"}]},
                     body="")
    ctx = build_draft_context(store, doc, {"summary": "概要"}, [{"pov": "甲", "event": "拜师"}],
                               "", {"character_names": ["甲"]}, recent_draft="")
    assert "句式短促，少用长修饰。" in ctx.user

    # 手改 SKILL.md → 下次构建反映新值（无缓存陈旧）
    (info.path / ".harness" / "skills" / "style" / "SKILL.md").write_text(
        "---\nname: style\ndescription: 项目文风\n---\n\n改后：多用短句，禁用长定语。\n",
        encoding="utf-8")
    ctx2 = build_draft_context(store, doc, {"summary": "概要"}, [{"pov": "甲", "event": "拜师"}],
                                "", {"character_names": ["甲"]}, recent_draft="")
    assert "改后：多用短句，禁用长定语。" in ctx2.user
    assert "句式短促，少用长修饰。" not in ctx2.user


# ------------------------------------------------------------------ 场景④ supersede

async def test_scenario_supersede_no_inflation(tmp_path):
    """④ /audit 连跑两次同章 → pending 不翻倍（旧候选被 supersede）。"""
    from harness.audit.runner import run_audit

    info = create_project(tmp_path, "复扫书")
    store = ProjectStore(info.path)
    mem = MemoryStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一夜", "status": "done",
                                                "beats": [{"pov": "乙"}]},
                                   body="乙走进山门。甲倒在血泊里。"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "第二夜", "status": "done",
                                                "beats": [{"pov": "乙"}]},
                                   body="乙走进山门。甲的牌位立在堂上。"))
    mem.apply_state_updates({"characters": [{"name": "甲", "status": "dead"}]}, seq=1)

    llm = RoutingLLM([(lambda p: "【待审正文】" in p, "[]")])
    prompts = PromptLoader(project_root=info.path)
    for _ in range(2):
        await run_audit(store=store, mem=mem, llm=llm, prompts=prompts,
                        settings=make_settings(), chapters=[2], emit=lambda k, p: None)

    issues = IssuesStore(info.path)
    pending = issues.list("pending")
    resolved = issues.list("resolved")
    assert len(pending) == 1                # 不翻倍
    assert len(resolved) == 1                # 旧候选被 supersede
    assert pending[0].id != resolved[0].id
