import json
from pathlib import Path

from harness.cli import main
from harness.projects import create_project
from harness.session import Components
from harness.store.project import ProjectStore


def test_non_tty_without_path_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main([]) == 2
    assert "harness <路径>" in capsys.readouterr().err


def test_direct_path_opens_repl(tmp_path, monkeypatch):
    info = create_project(tmp_path, "直达书")
    inputs = iter(["/state", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0


def test_selector_new_project(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    root = tmp_path / "novels"
    inputs = iter(["n", "新建书", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main(["--library", str(root)]) == 0
    assert (root / "新建书" / "README.md").exists()


def test_bad_path_exits_nonzero_with_clean_stderr(tmp_path, capsys):
    bad = tmp_path / "不存在路径"
    assert main([str(bad)]) == 1
    err = capsys.readouterr().err
    assert str(bad) in err and "harness <项目路径>" in err
    assert "Traceback" not in err


def test_broken_chapter_still_serves_state(tmp_path, monkeypatch, capsys):
    from harness.store.project import ChapterDoc

    info = create_project(tmp_path, "坏章书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "好章", "status": "done"}, body="x"))
    (info.path / "chapters" / "0002-坏章.md").write_text("---\nseq: [坏\n---\n正文\n", encoding="utf-8")
    inputs = iter(["/state", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "好章" in out                       # 好章照常列出
    assert "0002-坏章.md" in out               # 坏章降级为警告行（点名文件），REPL 不死


def test_state_view_shows_project(tmp_path, monkeypatch, capsys):
    info = create_project(tmp_path, "展示书", {"genre": "科幻"})
    store = ProjectStore(info.path)
    store.upsert_world_entries([{"title": "星港", "category": "地理", "summary": "s", "content": "c", "importance": "中"}])
    inputs = iter(["/state", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    main([str(info.path)])
    out = capsys.readouterr().out
    assert "展示书" in out and "星港" in out


def test_plain_text_gets_stub_notice(tmp_path, monkeypatch, capsys):
    info = create_project(tmp_path, "stub书")
    inputs = iter(["帮我写第一章", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    main([str(info.path)])
    assert "P1" in capsys.readouterr().out


def test_memory_command_tolerates_non_utf8_state(tmp_path, capsys):
    """M-d：state.md 非 UTF-8 → /memory 速览不炸，如实注记不可读（守卫同 read_state 口径）。"""
    from harness.repl import _cmd_memory
    info = create_project(tmp_path, "坏快照书")
    store = ProjectStore(info.path)
    (store.root / "memory" / "state.md").write_bytes(b"\xff\xfe\x00bad")
    _cmd_memory(store)                              # 不 raise
    out = capsys.readouterr().out
    assert "现状快照不可读" in out
    assert "【章记忆】" in out                       # 后续节照常速览


def test_audit_command_parses_args():
    """/audit 参数解析：无参=None（LLM 最近 12 章）/all=全部定稿章/单章/区间；非法 raise。"""
    import pytest

    from harness.repl import _parse_audit_arg
    done = [1, 2, 3, 5]
    assert _parse_audit_arg("", done) is None
    assert _parse_audit_arg("all", done) == [1, 2, 3, 5]
    assert _parse_audit_arg("5", done) == [5]
    assert _parse_audit_arg("2-3", done) == [2, 3]
    for bad in ("abc", "0", "3-2", "-1", "1-", "1-2-3", "alll"):
        with pytest.raises(ValueError):
            _parse_audit_arg(bad, done)


def test_audit_command_unknown_arg_errors_without_crash(tmp_path, monkeypatch, capsys):
    """REPL 层：/audit 非法参数 → 报错提示不炸，会话继续。"""
    info = create_project(tmp_path, "参数书")
    inputs = iter(["/audit bogus", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "/audit" in out                       # 报错点名命令用法


def test_audit_command_without_provider_exits_honestly(tmp_path, monkeypatch, capsys):
    """非 TTY/未配 provider：/audit 如实提示「未配置 provider」，不炸不静默。"""
    info = create_project(tmp_path, "无provider书")
    inputs = iter(["/audit", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "未配置 provider" in out


# ------------------------------------------------------------------ /check（P6 Task 4）

def test_check_command_parses_args():
    """/check 参数解析：无参=全书级总编辑 / plot=情节级 / editor=签约责编视角；非法 raise。"""
    import pytest

    from harness.repl import _parse_check_arg

    assert _parse_check_arg("") == ("global", False)
    assert _parse_check_arg("global") == ("global", False)
    assert _parse_check_arg("plot") == ("plot", False)
    assert _parse_check_arg("global editor") == ("global", True)
    # 责编视角只对全书级有效 → 「plot editor」直接拒（不静默丢弃 editor 请求）
    with pytest.raises(ValueError):
        _parse_check_arg("plot editor")
    with pytest.raises(ValueError):
        _parse_check_arg("editor plot")
    for bad in ("bogus", "plot bogus"):
        with pytest.raises(ValueError):
            _parse_check_arg(bad)


def test_check_plot_editor_rejected_honestly(tmp_path, monkeypatch, capsys):
    """REPL 层：/check plot editor → 如实报错（责编视角只对全书级有效），不静默丢弃。"""
    info = create_project(tmp_path, "责编书")
    inputs = iter(["/check plot editor", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "责编视角" in out
    assert "全书级" in out


def test_check_command_without_provider_exits_honestly(tmp_path, monkeypatch, capsys):
    """非 TTY/未配 provider：/check 如实提示「验收未执行」，不炸不静默（不假装跑过）。"""
    info = create_project(tmp_path, "验收书")
    inputs = iter(["/check", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "验收未执行" in out
    assert "未配置 provider" in out


def test_check_command_invalid_arg_errors_without_crash(tmp_path, monkeypatch, capsys):
    """REPL 层：/check 非法参数 → 报错提示不炸，会话继续。

    断言**错误文案本身**（旧写法只断言 "/check" in out——命令不存在时 repl 打的
    「未知命令：/check」也含 "/check" → 照样通过，等于没断言）。
    """
    info = create_project(tmp_path, "参数书")
    inputs = iter(["/check bogus", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "参数非法" in out
    assert "用法：/check" in out
    assert "未知命令" not in out


def _fake_check_llm(recorded: list):
    """假 LLM：回一条带**真 quote** 的验收意见（recorded 收提示词，供路由断言）。"""
    import json

    from harness.config import ProviderConfig

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.usage = None

    class FakeLLM:
        def __init__(self):
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature, max_tokens):
            recorded.append(messages[-1]["content"])
            return _Resp(json.dumps({
                "verdict": "fix",
                "issues": [{"chapter": "第1章", "type": "character", "dimension": "人物",
                            "quote": "少年握紧了手里的木剑",
                            "issue": "少年动机不明", "fix": "补一句回忆"}],
            }, ensure_ascii=False))

    return FakeLLM()


def _check_project(tmp_path):
    """一个有一章定稿正文的项目（供 /check 真跑路径）。"""
    from harness.store.project import ChapterDoc

    info = create_project(tmp_path, "真跑书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜",
                                                 "status": "done", "objective": "推进主线"},
                                    body="第一夜，风雪压塌了山门。少年握紧了手里的木剑。"))
    return info


def test_check_command_runs_with_fake_llm(tmp_path, monkeypatch, capsys):
    """/check 真跑路径（假 LLM）：观测如实打出来、带 quote 的意见落 issues。"""
    from harness.run.issues import IssuesStore

    recorded: list = []
    info = _check_project(tmp_path)
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=_fake_check_llm(recorded),
                                                registry=None, ui=None))
    inputs = iter(["/check", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "验收完成" in out
    pending = IssuesStore(info.path).list("pending")
    assert len(pending) == 1
    assert pending[0].source == "check"
    assert pending[0].chapter == 1


def test_check_command_emits_single_check_report(tmp_path, monkeypatch, capsys):
    """REPL 的 /check（直调编排层）→ events.log 里**只有一条** check_report（不重复）。"""
    from harness.run.events import EventLog

    recorded: list = []
    info = _check_project(tmp_path)
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=_fake_check_llm(recorded),
                                                registry=None, ui=None))
    inputs = iter(["/check", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    reports = [e for e in EventLog(info.path).tail(500) if e["kind"] == "check_report"]
    assert len(reports) == 1
    assert reports[0]["payload"]["kind"] == "global"


def test_check_command_plot_uses_plot_prompt(tmp_path, monkeypatch, capsys):
    """/check plot → 渲染 task.auto_plot_check（检查官模板）。"""
    recorded: list = []
    info = _check_project(tmp_path)
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=_fake_check_llm(recorded),
                                                registry=None, ui=None))
    inputs = iter(["/check plot", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "你是自主创作流程的检查官" in recorded[0]
    assert "签约责编" not in recorded[0]


def test_check_command_global_editor_uses_editor_prompt(tmp_path, monkeypatch, capsys):
    """/check global editor → 渲染 task.auto_editor_review（签约责编模板）。"""
    recorded: list = []
    info = _check_project(tmp_path)
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=_fake_check_llm(recorded),
                                                registry=None, ui=None))
    inputs = iter(["/check global editor", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "签约责编" in recorded[0]


def test_check_command_help_line_lists_check():
    """/help 里列得出 /check（命令自解释）。"""
    from harness.repl import _HELP

    assert "/check" in _HELP


def test_check_command_closes_llm_client(tmp_path, monkeypatch, capsys):
    """/check 结束后关闭**本次新建的** llm 客户端（防连接泄漏；aclose 幂等）。"""
    from harness.config import ProviderConfig

    closed: list[bool] = []

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.usage = None

    class _ClosableLLM:
        def __init__(self):
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                            lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature, max_tokens):
            return _Resp(json.dumps({"verdict": "pass", "issues": []}, ensure_ascii=False))

        async def aclose(self):
            closed.append(True)

    info = _check_project(tmp_path)
    monkeypatch.setattr("harness.session.RunSession.components",
                        lambda self: Components(llm=_ClosableLLM(),
                                                registry=None, ui=None))
    inputs = iter(["/check", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert closed == [True]


def test_issues_fix_branch_without_provider_exits_honestly(tmp_path, monkeypatch, capsys):
    """「交给 AI 修」（P5 T5）：未配 provider/非 TTY → 如实提示，issue 保持 pending。

    确认门用假 ApprovalUI（返回第三项「交给 AI 修」）驱动分支——真 ApprovalUI 在非 TTY
    下走 pending.md 文件协议，会阻塞等待，不适合本用例。
    """
    from harness.run.approval import Choice
    from harness.run.issues import IssuesStore

    class _FakeUI:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, question, options, multi=False, note=None):
            return Choice(indices=[options.index("交给 AI 修")])

    monkeypatch.setattr("harness.session.ApprovalUI", _FakeUI)
    info = create_project(tmp_path, "issues书")
    IssuesStore(info.path).add("audit", "时间锚点错位", "第 1 章提到黎明", "夜风吹过山寨",
                                chapter=1)
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "交给 AI 修未执行" in out
    assert len(IssuesStore(info.path).list("pending")) == 1   # 保持 pending


def test_skills_command_lists_two_scopes(tmp_path, monkeypatch, capsys):
    """/skills（P5 T6）：两级清单（项目级 + 用户级，项目级覆盖同名）。

    用户级目录走默认 ``user_skills_dir()``——测试必须隔离真实 ``~/.harness/skills``。
    """
    info = create_project(tmp_path, "技能书")
    project_skill = info.path / ".harness" / "skills" / "master_plan"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text(
        "---\nname: master_plan\ndescription: 卷骨架方法论\n---\n\n正文要点\n", encoding="utf-8")
    home = tmp_path / "home_skills"
    home.mkdir()
    (home / "gold").mkdir()
    (home / "gold" / "SKILL.md").write_text(
        "---\nname: gold\ndescription: 情节准则\n---\n\n正文要点\n", encoding="utf-8")
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter(["/skills", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert "master_plan（project）：卷骨架方法论" in out
    assert "gold（user）：情节准则" in out


# ------------------------------------------------------------------ /skill import（P5 T8）

def _write_external_skill(base: Path, dirname: str, *, name: str = "gold",
                          description: str = "情节准则", body: str = "正文要点",
                          extra: dict | None = None) -> Path:
    """造一个外部 skill 源（目录形态，内含 SKILL.md + 可选附属文件）。"""
    d = base / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8")
    for filename, content in (extra or {}).items():
        (d / filename).write_text(content, encoding="utf-8")
    return d


def test_skill_import_file_to_user_scope(tmp_path, monkeypatch, capsys):
    """导入单个 SKILL.md → 落用户级 <user_dir>/<name>/SKILL.md。"""
    info = create_project(tmp_path, "导入书")
    src = tmp_path / "外部"
    src.mkdir()
    (src / "SKILL.md").write_text(
        "---\nname: gold\ndescription: 情节准则\n---\n\n正文要点\n", encoding="utf-8")
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {src / 'SKILL.md'}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    out = capsys.readouterr().out
    assert (home / "gold" / "SKILL.md").exists()
    assert "已导入 skill「gold」（用户级）" in out
    assert "改动即生效" in out


def test_skill_import_directory(tmp_path, monkeypatch, capsys):
    """导入整目录 → 整目录复制（附属文件同落）。"""
    info = create_project(tmp_path, "导入书")
    src = _write_external_skill(tmp_path, "外部目录", extra={"参考.md": "附带资料"})
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {src}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert (home / "gold" / "SKILL.md").exists()
    assert (home / "gold" / "参考.md").read_text(encoding="utf-8") == "附带资料"


def test_skill_import_project_flag(tmp_path, monkeypatch, capsys):
    """--project → 落项目级 .harness/skills/<name>/。"""
    info = create_project(tmp_path, "导入书")
    src = _write_external_skill(tmp_path, "外部")
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {src} --project", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert (info.path / ".harness" / "skills" / "gold" / "SKILL.md").exists()
    assert not (home / "gold").exists()
    assert "项目级" in capsys.readouterr().out


def test_skill_import_overwrite_confirm(tmp_path, monkeypatch, capsys):
    """同名已存在 → 确认卡：确认则覆盖、取消则不动。"""
    from harness.run.approval import Choice

    info = create_project(tmp_path, "导入书")
    src = _write_external_skill(tmp_path, "外部", body="新内容")
    home = tmp_path / "home_skills"
    (home / "gold").mkdir(parents=True)
    (home / "gold" / "SKILL.md").write_text("旧内容", encoding="utf-8")
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    class _ConfirmUI:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, question, options, multi=False, note=None):
            return Choice(indices=[options.index("覆盖")])

    monkeypatch.setattr("harness.session.ApprovalUI", _ConfirmUI)
    inputs = iter([f"/skill import {src}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "新内容" in (home / "gold" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_import_overwrite_cancelled(tmp_path, monkeypatch, capsys):
    """确认卡取消 → 原文件不动。"""
    from harness.run.approval import Choice

    info = create_project(tmp_path, "导入书")
    src = _write_external_skill(tmp_path, "外部", body="新内容")
    home = tmp_path / "home_skills"
    (home / "gold").mkdir(parents=True)
    (home / "gold" / "SKILL.md").write_text("旧内容", encoding="utf-8")
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    class _CancelUI:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, question, options, multi=False, note=None):
            return Choice(indices=[options.index("取消")])

    monkeypatch.setattr("harness.session.ApprovalUI", _CancelUI)
    inputs = iter([f"/skill import {src}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert (home / "gold" / "SKILL.md").read_text(encoding="utf-8") == "旧内容"
    assert "已取消" in capsys.readouterr().out


def test_skill_import_missing_path(tmp_path, monkeypatch, capsys):
    """路径不存在 → 如实报错，不炸。"""
    info = create_project(tmp_path, "导入书")
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {tmp_path / '不存在'}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "路径不存在" in capsys.readouterr().out


def test_skill_import_dir_without_skill_md(tmp_path, monkeypatch, capsys):
    """目录内无 SKILL.md → 如实报错，不炸。"""
    info = create_project(tmp_path, "导入书")
    empty = tmp_path / "空目录"
    empty.mkdir()
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {empty}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "SKILL.md" in capsys.readouterr().out


def test_skill_import_usage_without_path(tmp_path, monkeypatch, capsys):
    """缺路径 → 打用法，不炸。"""
    info = create_project(tmp_path, "导入书")
    inputs = iter(["/skill import", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0
    assert "用法：/skill import" in capsys.readouterr().out


def test_skill_import_name_cannot_escape_skills_dir(tmp_path, monkeypatch, capsys):
    """导入的 frontmatter name 含 '..' → 落点仍在 skills 目录内（不得越界写出）。"""
    info = create_project(tmp_path, "导入书")
    src = tmp_path / "外部"
    src.mkdir()
    (src / "SKILL.md").write_text(
        "---\nname: ../../escape\ndescription: 越界名\n---\n\n正文要点\n", encoding="utf-8")
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {src / 'SKILL.md'}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    assert (home / "escape" / "SKILL.md").exists()      # 收敛成单个路径段
    assert not (tmp_path / "escape").exists()           # 没写出 skills 目录
    assert not (home.parent / "escape").exists()


# ------------------------------------------------------------------ /issues 通道（P6 Task 5）

def _issue_ui(picks: list[str]):
    """假审批门：按 ask 出现顺序挑选项，并记下每次的问题与选项（供断言）。

    真 ``ApprovalUI`` 在非 TTY 下走 pending.md 文件协议会**阻塞**——本文件的 /issues
    用例一律用这个假 UI 驱动（照既有 ``_FakeUI`` / ``_ConfirmUI`` 的手法）。
    """
    from harness.run.approval import Choice

    asked: list[tuple[str, list[str]]] = []

    class _UI:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self, question, options, multi=False, note=None):
            asked.append((question, list(options)))
            return Choice(indices=[options.index(picks[len(asked) - 1])])

    return _UI, asked


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.usage = None


def _fake_fix_llm(script: list[str]):
    """假 LLM：顺序吐脚本（先改写替换文本，再记忆提取载荷）。"""
    from harness.config import ProviderConfig

    class FakeLLM:
        def __init__(self):
            self._script = list(script)
            self.calls: list[str] = []
            self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                           lite="m-lite", standard="m-std", pro="m-pro")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            self.calls.append(messages[-1]["content"])
            return _FakeResp(self._script.pop(0))

    return FakeLLM()


#: 记忆提取的五键载荷（重提取要它才落盘）
_EXTRACT_PAYLOAD = json.dumps({
    "events": [{"title": "风雪夜守门", "summary": "少年守住山门", "location": "山门",
                "time_marker": "深夜", "characters": ["少年"],
                "new_info": ["布防图"], "foreshadow": "后山密道"}],
    "character_events": [], "info_updates": [], "promise_updates": [],
    "state_updates": {"characters": [], "world": []},
}, ensure_ascii=False)


def test_issues_shows_source(tmp_path, monkeypatch, capsys):
    """/issues 按来源分组：审计意见与验收意见各归各组（组标题 + 每条带来源标识）。"""
    from harness.run.issues import IssuesStore

    info = create_project(tmp_path, "来源书")
    issues = IssuesStore(info.path)
    issues.add("audit", "时间锚点错位", "第 1 章提到黎明", "夜风吹过山寨", chapter=1)
    issues.add("check", "少年动机不明", "人物：少年动机不明；建议：补一句回忆",
               "少年握紧了手里的木剑", chapter=1)

    _ui, _asked = _issue_ui(["忽略", "忽略"])
    monkeypatch.setattr("harness.session.ApprovalUI", _ui)
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    out = capsys.readouterr().out
    assert "【一致性审计】" in out and "【验收】" in out      # 两组各有一个小标题
    assert "（audit）" in out and "（check）" in out          # 每条仍带来源标识
    assert "时间锚点错位" in out and "少年动机不明" in out
    # 审计组在前、验收组在后——两类候选不混作一列
    assert out.index("【一致性审计】") < out.index("【验收】")


def test_global_issue_offers_no_ai_fix(tmp_path, monkeypatch, capsys):
    """全局类意见（quote 为空）→ **没有**「交给 AI 修」（没有可定位的原文片段）。"""
    from harness.run.issues import IssuesStore

    info = create_project(tmp_path, "全局书")
    IssuesStore(info.path).add("check", "全书主线偏弱", "情节：全书主线偏弱；建议：加重主线")

    _ui, asked = _issue_ui(["忽略"])
    monkeypatch.setattr("harness.session.ApprovalUI", _ui)
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    assert asked, "应至少问过一次处置"
    _question, options = asked[0]
    assert "交给 AI 修" not in options
    assert options == ["已解决", "忽略"]
    out = capsys.readouterr().out
    assert "全局类意见（无原文片段）" in out        # 如实说明为什么少一个选项
    assert "只能标记已解决或忽略" in out
    # M-7：不得承诺台账里**不存在**的功能（「给已有意见补 quote」的入口没有）
    assert "补充可定位的片段" not in out
    assert "手工修改" in out


def test_check_issue_can_be_resolved(tmp_path, monkeypatch, capsys):
    """验收意见（source="check"）→ 可标记 resolved（已解决/忽略语义不动）。"""
    from harness.run.issues import IssuesStore

    info = create_project(tmp_path, "验收处置书")
    IssuesStore(info.path).add("check", "少年动机不明", "人物：动机不明",
                               "少年握紧了手里的木剑", chapter=1)

    _ui, _asked = _issue_ui(["已解决"])
    monkeypatch.setattr("harness.session.ApprovalUI", _ui)
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    store = IssuesStore(info.path)
    assert store.list("pending") == []
    assert store.list("resolved")[0].source == "check"
    assert "已标记 resolved" in capsys.readouterr().out


def test_check_issue_can_be_fixed(tmp_path, monkeypatch, capsys):
    """验收意见**有 quote** → 可走「交给 AI 修」：正文落盘改写 + issue resolved。"""
    from harness.run.issues import IssuesStore
    from harness.store.project import ChapterDoc, ProjectStore

    monkeypatch.setenv("USERPROFILE", str(tmp_path))    # Path.home() 隔离（提示词/全局配置）
    monkeypatch.setenv("HOME", str(tmp_path))
    info = create_project(tmp_path, "验收修复书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "风雪夜", "status": "done"},
                                    body="第一夜，风雪压塌了山门。少年握紧了手里的木剑。"))
    issue = IssuesStore(info.path).add("check", "少年动机不明", "人物：少年动机不明",
                                       "少年握紧了手里的木剑", chapter=1)
    # 项目级配置：给一个假 provider（记忆重提取要 provider 才跑得动）
    (info.path / ".harness" / "config.toml").write_text(
        'default_provider = "test"\ndefault_tier = "standard"\n\n'
        '[providers.test]\nbase_url = "https://api.test"\napi_key = "k"\n'
        'lite = "m-lite"\nstandard = "m-std"\npro = "m-pro"\n', encoding="utf-8")

    llm = _fake_fix_llm([json.dumps({"replacement": "少年攥紧木剑，望向山门外的风雪"},
                                     ensure_ascii=False), _EXTRACT_PAYLOAD])
    _ui, _asked = _issue_ui(["交给 AI 修", "应用修复"])
    monkeypatch.setattr("harness.session.ApprovalUI", _ui)
    monkeypatch.setattr("harness.session.RunSession.components",
                    lambda self: Components(llm=llm, registry=None, ui=None))
    inputs = iter(["/issues", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    body = store.read_chapter(1).body
    assert "少年攥紧木剑，望向山门外的风雪" in body     # 改写落盘
    assert "少年握紧了手里的木剑" not in body
    store_issues = IssuesStore(info.path)
    assert store_issues.list("pending") == []
    assert store_issues.list("resolved")[0].id == issue.id
    assert "已修改第1章并标记 resolved" in capsys.readouterr().out


async def test_fix_issue_tells_global_apart_from_unlocatable(tmp_path):
    """「交给 AI 修」的两种「定位不到」要分开说：

    - 全局类意见（**本来就没有** quote）→ 全局类文案；
    - 有 quote 但正文里找不到（正文被改过/引文不准）→ 既有文案**逐字不变**。
    """
    from harness.audit.fix import fix_issue
    from harness.config import ProviderConfig, Settings
    from harness.memory.store import MemoryStore
    from harness.projects import create_project
    from harness.prompts.loader import PromptLoader
    from harness.run.issues import IssuesStore
    from harness.store.project import ChapterDoc, ProjectStore

    info = create_project(tmp_path, "定位书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "第一章", "status": "done"},
                                    body="第一夜，风雪压塌了山门。"))
    issues = IssuesStore(info.path)
    mem = MemoryStore(info.path)
    prompts = PromptLoader(project_root=info.path, home_dir=info.path)
    settings = Settings(library_root=Path("x"), providers={}, default_provider="",
                        default_tier="standard", web_verify=True)

    class _NeverCalledLLM:
        provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            raise AssertionError("定位不到就不该调 LLM")

    class _NeverAskedUI:
        def ask(self, question, options, multi=False, note=None):
            raise AssertionError("定位不到就不该问确认卡")

    global_issue = issues.add("check", "全书主线偏弱", "情节：主线偏弱", None)
    text = await fix_issue(store=store, mem=mem, llm=_NeverCalledLLM(), prompts=prompts,
                           settings=settings, issue=global_issue, ui=_NeverAskedUI())
    assert text == "该意见是全局类（无原文片段），无法定位改写——issue 保持 pending"
    assert issues.list("pending")[0].id == global_issue.id

    missing = issues.add("check", "时间锚点错位", "详情", "正文里根本不存在的片段", chapter=1)
    text = await fix_issue(store=store, mem=mem, llm=_NeverCalledLLM(), prompts=prompts,
                           settings=settings, issue=missing, ui=_NeverAskedUI())
    assert text == "quote 已不可定位，issue 保持 pending"
    assert missing.id in [i.id for i in issues.list("pending")]   # 两条都保持 pending
