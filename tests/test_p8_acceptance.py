# P8 按章聚合修订的集成验收（REPL /fix 全链路）
"""端到端三场景（走 REPL 的 ``/fix``，假 LLM + 假确认门）：

① **按章聚合**：同一章两条意见 → **一次**确认卡 → **一次**写盘 → 两处都改 → **两条**都
   resolved（每章只修一轮，不反复喂回）；
② **取消**：确认卡选「取消」→ 零写盘、两条都保持 pending；
③ **不参与的意见**：全局类意见（无原文片段）→ 如实说「无可聚合修订的意见」，不假装跑过。
"""
from __future__ import annotations

import json

from harness.cli import main
from harness.config import ProviderConfig
from harness.projects import create_project
from harness.run.approval import ApprovalUI, Choice
from harness.run.issues import IssuesStore
from harness.session import Components
from harness.store.project import ChapterDoc, ProjectStore

BODY = "第一夜，风雪压塌了山门。少年握紧了手里的木剑。老者递来一盏冷酒。"
QUOTE_A = "少年握紧了手里的木剑"
QUOTE_B = "老者递来一盏冷酒"
NEW_A = "少年攥紧了手里那柄生了锈的木剑"
NEW_B = "老者无声递来一盏冷酒"


def _project(tmp_path):
    info = create_project(tmp_path, "聚合修订书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜",
                                                 "status": "done", "objective": "推进主线"},
                                    body=BODY))
    return info, store


def _fake_llm(script):
    """顺序吐脚本的假 llm（形态同 tests/test_cli_repl.py）。"""

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.usage = None

    class FakeLLM:
        def __init__(self):
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m", standard="m", pro="m")
            self.calls: list[str] = []

        async def complete(self, messages, *, model, temperature, max_tokens):
            self.calls.append(messages[-1]["content"])
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Resp(item)

    return FakeLLM()


def _fake_ui(indices=(0,)):
    """假确认门：记录问题文本，返回指定选项。"""
    seen: list[str] = []

    def ask(question, options, multi=False, note=None):
        seen.append(question)
        return Choice(indices=list(indices))

    return ApprovalUI(ask=ask), seen


def _fix_payload(*pairs):
    return json.dumps({"replacements": [{"id": i, "replacement": r} for i, r in pairs]},
                      ensure_ascii=False)


def _extract_payload():
    return json.dumps({
        "events": [{"title": "风雪夜", "summary": "少年在山门独守", "location": "山门",
                    "time_marker": "第一夜", "characters": ["少年"],
                    "new_info": ["木剑"], "foreshadow": ""}],
        "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [], "world": []},
    }, ensure_ascii=False)


def test_fix_command_aggregates_one_chapter_one_card(tmp_path, monkeypatch, capsys):
    """两条意见同章 → 一张卡 → 一次写盘 → 两处都改 → 两条都 resolved。"""
    info, store = _project(tmp_path)
    issues = IssuesStore(info.path)
    a = issues.add("check", "少年动机不明", "该补一句回忆", QUOTE_A, chapter=1)
    b = issues.add("check", "老者出场突兀", "补一句交代", QUOTE_B, chapter=1)
    ui, seen = _fake_ui()
    llm = _fake_llm([_fix_payload((a.id, NEW_A), (b.id, NEW_B)), _extract_payload()])
    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(llm=llm, registry=None, ui=ui))
    inputs = iter(["/fix", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out

    assert len(seen) == 1                       # **一章一张**确认卡
    assert "少年动机不明" in seen[0] and "老者出场突兀" in seen[0]
    assert "已修改第1章" in out
    body = store.read_chapter(1).body
    assert NEW_A in body and NEW_B in body
    assert QUOTE_A not in body and QUOTE_B not in body
    assert body.startswith("第一夜，风雪压塌了山门。")   # 未命中的部分逐字不动
    assert issues.list("pending") == []
    assert [i.id for i in issues.list("resolved")] == [a.id, b.id]


def test_fix_command_cancel_keeps_pending(tmp_path, monkeypatch, capsys):
    """确认卡选「取消」→ 零写盘、两条都保持 pending。"""
    info, store = _project(tmp_path)
    issues = IssuesStore(info.path)
    a = issues.add("check", "少年动机不明", "该补一句回忆", QUOTE_A, chapter=1)
    b = issues.add("check", "老者出场突兀", "补一句交代", QUOTE_B, chapter=1)
    ui, _seen = _fake_ui(indices=(1,))
    llm = _fake_llm([_fix_payload((a.id, NEW_A), (b.id, NEW_B))])
    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(llm=llm, registry=None, ui=ui))
    inputs = iter(["/fix", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main([str(info.path)]) == 0

    assert store.read_chapter(1).body == BODY
    assert [i.id for i in issues.list("pending")] == [a.id, b.id]


def test_fix_command_without_quotable_issues_says_so(tmp_path, monkeypatch, capsys):
    """只有全局类意见（无原文片段）→ 如实说「无可聚合修订的意见」，不假装跑过。"""
    info, store = _project(tmp_path)
    issues = IssuesStore(info.path)
    issues.add("check", "全书节奏偏慢", "中段该收紧", None, chapter=None)
    ui, seen = _fake_ui()
    llm = _fake_llm([])
    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(llm=llm, registry=None, ui=ui))
    inputs = iter(["/fix", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out

    assert "无可聚合修订的意见" in out
    assert seen == []                            # 没有可聚合的意见 → 一张卡都不出
    assert llm.calls == []                       # 也没有白跑一次 LLM
    assert store.read_chapter(1).body == BODY
    assert len(issues.list("pending")) == 1


def test_fix_command_two_chapters_two_cards(tmp_path, monkeypatch, capsys):
    """两章各有意见 → **两张**卡（一章一张），两章各改各的。"""
    info, store = _project(tmp_path)
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "山门",
                                                 "status": "done", "objective": "推进主线"},
                                    body="第二夜，少年在山门外的雪地里坐了很久。"))
    issues = IssuesStore(info.path)
    a = issues.add("check", "少年动机不明", "该补一句回忆", QUOTE_A, chapter=1)
    b = issues.add("check", "第二夜太静", "补一句风声", "少年在山门外的雪地里坐了很久", chapter=2)
    ui, seen = _fake_ui()
    llm = _fake_llm([
        _fix_payload((a.id, NEW_A)), _extract_payload(),
        _fix_payload((b.id, "少年在山门外的雪地里坐了很久，风声呜咽。")), _extract_payload(),
    ])
    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(llm=llm, registry=None, ui=ui))
    inputs = iter(["/fix", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    assert main([str(info.path)]) == 0

    assert len(seen) == 2                       # 一章一张卡
    assert "第1章" in seen[0] and "第2章" in seen[1]
    assert NEW_A in store.read_chapter(1).body
    assert "风声呜咽" in store.read_chapter(2).body
    assert issues.list("pending") == []


def test_fix_command_help_line_lists_fix():
    """/help 里列得出 /fix（命令自解释）。"""
    from harness.repl import _HELP

    assert "/fix" in _HELP
