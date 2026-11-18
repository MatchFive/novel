"""独立审核（2026-11-15）**第二批**缺陷的回归测试：诚实性/坏数据/纪律类。

逐条对应审核报告：
- D6：台账单条 issue JSON **缺键** → 跳过（不 KeyError 直穿）
- M8：坏 ``arcs.md`` → 进度摘要**看得见**（不是静默的「卷（0）」）
- L4：``master_plan`` **0 成功**不说「完成」（否则分类器判 ok → agent 以为成功了）
- D4：观测里的**载荷文字**（含「连接」等常用词）不再让**成功**的观测被判 transient
- L3：只读工具的 registry 调用走 ``asyncio.to_thread``（300 章建索引不再堵事件循环）
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.agent.loop import AgentLoop
from harness.run.issues import IssuesStore
from harness.store.project import ProjectStore

# ------------------------------------------------------------------ D6

def test_d6_issue_json_missing_key_is_skipped(tmp_path):
    """**D6**：台账里某条 issue 的 JSON **缺键** → **跳过**（不 KeyError 直穿）。

    台账是**用户可手改**的文件；缺一个 ``detail`` 不该让 ``/issues``、
    ``run_check`` 的 supersede、``/audit`` 落库、``/fix`` 分组全部中招。
    """
    issues_dir = tmp_path / ".harness" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "0001.json").write_text(json.dumps(
        {"id": "0001", "source": "check", "title": "好的一条", "detail": "详情",
         "status": "pending"}, ensure_ascii=False), encoding="utf-8")
    (issues_dir / "0002.json").write_text(
        json.dumps({"id": "0002", "status": "pending"}, ensure_ascii=False),
        encoding="utf-8")      # 缺 source/title/detail → 坏档

    issues = IssuesStore(tmp_path).list("pending")

    assert [i.id for i in issues] == ["0001"]      # 坏的那条被跳过，好的一条照常


def test_d6_issue_json_broken_is_skipped(tmp_path):
    """坏 JSON 同样跳过（既有行为，别退化）。"""
    issues_dir = tmp_path / ".harness" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "0001.json").write_text("{ 不是 JSON", encoding="utf-8")
    (issues_dir / "0002.json").write_text(json.dumps(
        {"id": "0002", "source": "check", "title": "好的一条", "detail": "详情",
         "status": "pending"}, ensure_ascii=False), encoding="utf-8")

    issues = IssuesStore(tmp_path).list("pending")
    assert [i.id for i in issues] == ["0002"]


# ------------------------------------------------------------------ M8

def test_m8_broken_arcs_shows_in_progress_digest(tmp_path):
    """**M8**：坏 ``arcs.md`` → 进度摘要里**看得见**（不是静默的「卷（0）」）。

    只 log.warning 的话，agent 看到「卷（0）」→ 以为没有骨架 → 回头调 plan_arcs 重产
    → 正好撞上「坏 arcs.md 让规划链抛栈」（同一个坑的另一头）。
    """
    from harness.run.digest import progress_digest

    store = ProjectStore(tmp_path)
    arcs_md = store.root / "outline" / "arcs.md"
    arcs_md.parent.mkdir(parents=True, exist_ok=True)
    arcs_md.write_text("---\narcs: [1, 2\n---\n", encoding="utf-8")

    out = progress_digest(store)

    assert "卷骨架文件解析失败" in out
    assert "arcs.md" in out


# ------------------------------------------------------------------ L4

async def test_l4_zero_success_not_called_complete(tmp_path):
    """**L4**：全部骨架 ``vol`` 非 int → 0 成功 → 观测**不说「完成」**。

    原先的观测里没有「失败（」字样 → loop 的分类器判 **ok** → agent 以为规划成功了。
    """
    from harness.tools.plots import master_plan

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.usage = None

    class _LLM:
        @property
        def provider(self):
            from harness.config import ProviderConfig

            return ProviderConfig(base_url="https://api.test", api_key="k",
                                  lite="m", standard="m", pro="m")

        async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
            return _Resp(json.dumps({"plots": [{"title": "x", "summary": "s",
                                               "conflict": "c", "resolution": "r",
                                               "beats": ["起"]}]}))

    from harness.config import Settings

    store = ProjectStore(tmp_path)
    (store.root / "outline").mkdir(parents=True, exist_ok=True)
    (store.root / "outline" / "arcs.md").write_text(
        "---\narcs:\n  - {vol: 甲, title: 坏卷号}\n---\n", encoding="utf-8")

    result = await master_plan(
        store, _LLM(),
        __import__("harness.prompts.loader", fromlist=["PromptLoader"]).PromptLoader(
            home_dir=store.root),
        Settings(library_root=Path("x"),
                 providers={"t": _LLM().provider}, default_provider="t",
                 default_tier="standard", web_verify=True),
        lambda k, p: None, {})

    assert "完成" not in result.text
    assert "未成功" in result.text


# ------------------------------------------------------------------ D4

def test_d4_payload_words_do_not_break_success_classification():
    """**D4**：观测**载荷**里的常用词（「连接」）不再让**成功**的观测被判 transient。

    ``TRANSIENT_MARKERS`` 含「连接」——原先扫**整条**观测 → 一次**成功**的 web_search
    因结论里写「这条驿道连接南北两州」被判 transient → 该步不算有效步、白烧 noop 槽、
    连续几次即 ``paused(noop_streak)``。
    """
    ok_obs = ("· 核查「南北驿道」\n"
              "  这条驿道连接南北两州，商旅往来不绝。")
    assert AgentLoop._classify(ok_obs) == "ok"

    # 既有语义不变：状态行里的瞬时词 → 仍判 transient
    assert AgentLoop._classify("连接超时，请重试") == "transient"
    # 既有语义不变：失败观测的细节里出现瞬时词 → 仍判 transient
    assert AgentLoop._classify("失败（调用失败）：输出被截断") == "transient"
    # 既有语义不变：失败观测 → failed
    assert AgentLoop._classify("失败（参数）：args.query 缺参") == "failed"


def test_d4_web_search_observation_has_status_first_line(tmp_path):
    """web_search 的观测**第一行是状态行**（核查目标/结论都是用户/模型原话，可能含「连接」）。"""
    from harness.tools import web as web_mod

    src = Path(web_mod.__file__).read_text(encoding="utf-8")
    i = src.find("lines: list[str] = [")
    assert i > 0
    first = src[i:src.find("\n", i)]
    assert "联网核查完成" in first        # 状态行在最前
    assert "entry.conclusion" in src     # 结论仍在载荷里


# ------------------------------------------------------------------ L3

async def test_l3_registry_call_runs_off_the_event_loop():
    """**L3**：只读工具的 registry 调用**不在事件循环线程上**跑。

    ``search_chapters`` 每次调用都要建**全书内存索引**（读 300 个章文件 + trigram 分词
    + 建倒排）→ 同步跑在事件循环上会让主循环**假死数秒**（P4 I-1 纪律）。

    **行为测试**（不是扒源码）：记录 registry.execute 被调用时所在的**线程**，
    断言它**不是**事件循环所在的那个线程。（扒源码的写法会在并行分支里残留
    ``asyncio.to_thread`` 字样 → 变异不红 → 空测。）
    """
    import threading

    from harness.agent.loop import AgentLoop

    seen: dict = {}

    class _Reg:
        def execute(self, name, args, *, allowed):
            seen["thread"] = threading.current_thread()
            return "ok"

        def execute_parallel(self, calls, *, allowed):
            seen["thread"] = threading.current_thread()
            return ["ok"] * len(calls)

    class _Fake:
        registry = _Reg()
        _classify = staticmethod(AgentLoop._classify)

        def _record_result(self, name, obs):
            pass

        def _parallel_calls(self, response, allowed):
            return None

    loop_thread = threading.current_thread()
    obs, _outcome, _recorded, _per_tool = await AgentLoop._sync_tool(
        _Fake(), "search_chapters", {}, None, {"search_chapters"},
        cooled={}, step=0)

    assert obs == "ok"
    assert seen["thread"] is not loop_thread      # **不在事件循环线程上**
