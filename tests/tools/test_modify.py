"""edit_by_command：diff 确认门（确认前零写盘）/ 取消零变更 / 范围外拒绝 / 多选部分应用 / 坏档跳过。

LLM 全 mock（JSON body MockTransport）；ApprovalUI 注入假实现。
P3（Task 8）：edit_by_command 迁 ToolResult——观测文本/重试次数/扩额公式不变（registry
handler 解包 .text，既有断言原样在跑）；直接 await 模块级协程拿到 ToolResult，
usage 非 None 走 loop 同账入账路径。
"""
import json
from typing import ClassVar

import httpx
import pytest

from harness.config import ProviderConfig
from harness.llm.client import OpenAICompatClient
from harness.llm.policy import CAP_MAX_TOKENS
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, Choice
from harness.store.frontmatter import render_document
from harness.store.project import ChapterDoc, ProjectStore
from harness.tools.base import ToolResult
from harness.tools.modify import edit_by_command, register_modify_tools
from harness.tools.registry import ToolRegistry


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path, "测试书")
    s = ProjectStore(info.path)
    s.upsert_character({"name": "陆昭", "role_type": "protagonist", "aliases": ["昭哥"]})
    s.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇", "status": "done"}, body="正文"))
    return s


@pytest.fixture
def make_llm():
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
                           lite="m", standard="standard-model", pro="m"),
            http_client=http)
        return client, calls

    return factory


class FakeUI:
    """假审批：ask 时可挂字节校验（确认前零写盘的断言点）；可配置返回选择或取消。"""

    def __init__(self, choice=None, cancel=False, byte_check=None):
        self.asked = []
        self._choice = choice
        self._cancel = cancel
        self._byte_check = byte_check

    def ask(self, question, options, multi=False, note=None):
        self.asked.append((question, options, multi))
        if self._byte_check is not None:
            self._byte_check()
        if self._cancel:
            raise ApprovalCancelled()
        return self._choice


def make_registry(store, llm, ui):
    reg = ToolRegistry(store)
    register_modify_tools(reg, store=store, llm=llm,
                          prompts=PromptLoader(home_dir=store.root), ui=ui)
    return reg


def _alias_card(aliases):
    return render_document({"name": "陆昭", "role_type": "protagonist", "aliases": aliases},
                           "# 陆昭\n\n天才修士。")


def _char_bytes(store):
    return (store.root / "characters" / "陆昭.md").read_bytes()


def test_edit_by_command_applied_triggers_git_snapshot(store, make_llm, monkeypatch):
    """P35 挂点：应用成功（applied 非空）→ auto_snapshot（reason=批量设定变更）。"""
    import harness.snapshot as snap

    recorded = []
    monkeypatch.setattr(snap, "auto_snapshot",
                        lambda root, reason, detail="": recorded.append((reason, detail)) or "abc1234")
    edits = json.dumps([{"path": "characters/陆昭.md",
                         "content": _alias_card(["昭哥", "昭公子"])}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    reg.execute("edit_by_command", {"instruction": "给陆昭加别名昭公子"},
                allowed=frozenset({"edit_by_command"}))
    assert len(recorded) == 1
    reason, detail = recorded[0]
    assert reason == "批量设定变更"
    assert "1 个文件" in detail and "characters/陆昭.md" in detail


# ---------- P36（C2）：edit_by_command 范围扩到 .harness/skills/<name>/SKILL.md ----------

_SKILL_DOC = ("---\nname: 打斗场面\ndescription: 打斗场面的写法\n---\n\n"
              "# 打斗场面\n\n招式要有来有回。\n")


def test_edit_by_command_creates_project_skill(store, make_llm):
    """P36：skill 的 LLM 自动创建走既有轻量修改路径（设计 §2.6）——确认后直落
    ``.harness/skills/<name>/SKILL.md``；propose 提示词也能看到既有项目 skill。"""
    skill_dir = store.root / ".harness" / "skills" / "旧手法"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# 旧手法\n", encoding="utf-8")
    edits = json.dumps([{"path": ".harness/skills/打斗场面/SKILL.md",
                         "content": _SKILL_DOC}], ensure_ascii=False)
    llm, calls = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "新建打斗场面的 skill"},
                      allowed=frozenset({"edit_by_command"}))
    target = store.root / ".harness" / "skills" / "打斗场面" / "SKILL.md"
    assert target.read_text(encoding="utf-8") == _SKILL_DOC
    assert "已应用 1 个文件" in obs
    # propose 提示词的【现状】含既有项目 skill（模型可见才能扩建，不覆盖乱写）
    assert ".harness/skills/旧手法/SKILL.md" in calls[0]["messages"][-1]["content"]


def test_edit_by_command_rejects_illegal_skill_name(store, make_llm):
    """skill 名不合法（空格 / .. / 非 SKILL.md 段）→ 拒绝观测，零写盘。"""
    edits = json.dumps([{"path": ".harness/skills/坏 名/SKILL.md",
                         "content": _SKILL_DOC}], ensure_ascii=False)
    llm, _calls = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "新建 skill"},
                      allowed=frozenset({"edit_by_command"}))
    assert "skill 名非法" in obs
    assert not (store.root / ".harness" / "skills" / "坏 名").exists()
    assert len(ui.asked) == 0                              # 范围外拒绝在确认门之前
    edits2 = json.dumps([{"path": ".harness/skills/abc/notes.md",
                          "content": _SKILL_DOC}], ensure_ascii=False)
    llm2, _ = make_llm([edits2])
    reg2 = make_registry(store, llm2, ui)
    obs2 = reg2.execute("edit_by_command", {"instruction": "新建 skill"},
                        allowed=frozenset({"edit_by_command"}))
    assert "skill 路径必须是 .harness/skills/<name>/SKILL.md" in obs2


def test_edit_by_command_confirm_applies_alias(store, make_llm):
    old_bytes = _char_bytes(store)
    edits = json.dumps([{"path": "characters/陆昭.md",
                         "content": _alias_card(["昭哥", "昭公子"])}], ensure_ascii=False)
    llm, calls = make_llm([edits])

    def check_no_write_yet():
        assert _char_bytes(store) == old_bytes  # 确认前零写盘：ask 被调用的瞬间字节未变

    ui = FakeUI(choice=Choice(indices=[0]), byte_check=check_no_write_yet)
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "给陆昭加别名昭公子"},
                      allowed=frozenset({"edit_by_command"}))
    assert len(calls) == 1
    assert calls[0]["model"] == "standard-model"  # modify propose 走 standard 档
    assert len(ui.asked) == 1
    question, options, multi = ui.asked[0]
    assert multi is True                             # 逐文件可勾选
    assert "a/characters/陆昭.md" in question        # diff 预览已渲染进确认卡
    assert any("陆昭" in o for o in options)
    assert _char_bytes(store) != old_bytes           # 通过的文件经 store 写盘
    assert "昭公子" in store.read_character("陆昭")["aliases"]  # 别名落入角色卡
    assert "已应用 1 个文件" in obs
    assert reg.is_read_only("edit_by_command") is False


def test_edit_by_command_cancel_zero_changes(store, make_llm):
    old_bytes = _char_bytes(store)
    edits = json.dumps([{"path": "characters/陆昭.md",
                         "content": _alias_card(["昭哥", "昭公子"])}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(cancel=True, byte_check=lambda: _char_bytes(store) == old_bytes or pytest.fail("确认前已写盘"))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "给陆昭加别名昭公子"},
                      allowed=frozenset({"edit_by_command"}))
    assert "已取消" in obs and "零变更" in obs
    assert _char_bytes(store) == old_bytes           # 取消后字节不变


def test_edit_by_command_rejects_chapters_without_asking(store, make_llm):
    chapter = store.root / "chapters" / "0001-开篇.md"
    old_bytes = chapter.read_bytes()
    edits = json.dumps([{"path": "chapters/0001-开篇.md", "content": "篡改正文"}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "改正文"},
                      allowed=frozenset({"edit_by_command"}))
    assert ui.asked == []                            # 无范围内文件，确认门都不开
    assert "chapters" in obs and "P3" in obs
    assert chapter.read_bytes() == old_bytes


def test_edit_by_command_multi_select_applies_only_approved(store, make_llm):
    old_lu = _char_bytes(store)
    card_shen = render_document({"name": "沈青", "role_type": "supporting", "aliases": []}, "# 沈青\n\n")
    edits = json.dumps([
        {"path": "characters/陆昭.md", "content": _alias_card(["昭哥", "昭公子"])},
        {"path": "characters/沈青.md", "content": card_shen},
    ], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[1]))          # 只勾选第二个文件
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "加别名并新建沈青"},
                      allowed=frozenset({"edit_by_command"}))
    assert _char_bytes(store) == old_lu              # 未勾选的不写
    assert (store.root / "characters" / "沈青.md").exists()  # 勾选的已写
    assert store.read_character("沈青")["role_type"] == "supporting"
    assert "已应用 1 个文件" in obs


def test_edit_by_command_bad_frontmatter_skips_file(store, make_llm):
    edits = json.dumps([{"path": "characters/新角色.md",
                         "content": "---\nname: [unclosed\n---\n\n坏档"}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "新建角色"},
                      allowed=frozenset({"edit_by_command"}))
    assert not (store.root / "characters" / "新角色.md").exists()
    assert "新角色.md" in obs and "跳过" in obs and "front-matter" in obs


def test_edit_by_command_applies_arcs_md(store, make_llm):
    arcs_content = render_document(
        {"arcs": [{"vol": 1, "title": "崛起", "goal": "踏上修行路",
                   "summary": "s", "conflict": "c", "resolution": "r", "capacity_hint": "约40章"}]}, "")
    edits = json.dumps([{"path": "outline/arcs.md", "content": arcs_content}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "落卷骨架"},
                      allowed=frozenset({"edit_by_command"}))
    arcs = store.read_arcs()
    assert len(arcs) == 1 and arcs[0]["goal"] == "踏上修行路"
    assert "已应用 1 个文件" in obs


def test_edit_by_command_missing_instruction(store, make_llm):
    llm, calls = make_llm(["[]"])
    ui = FakeUI()
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {}, allowed=frozenset({"edit_by_command"}))
    assert "instruction" in obs
    assert calls == []
    assert ui.asked == []


def test_edit_by_command_applies_plots_md(store, make_llm):
    vol1 = "# 第1卷情节\n\n### 1. 夜袭\n- 状态: planned\n\n概要：趁夜夺城\n"
    edits = json.dumps([{"path": "outline/plots/vol-01.md", "content": vol1}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "落第一卷情节"},
                      allowed=frozenset({"edit_by_command"}))
    plots = store.read_plots(1)
    assert len(plots) == 1 and plots[0]["title"] == "夜袭" and plots[0]["summary"] == "趁夜夺城"
    assert "已应用 1 个文件" in obs


def test_edit_by_command_rejects_traversal(store, make_llm):
    edits = json.dumps([{"path": "../outside.md", "content": "逃逸"}], ensure_ascii=False)
    llm, _ = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "越权"},
                      allowed=frozenset({"edit_by_command"}))
    assert ui.asked == []
    assert "拒绝" in obs and "越界" in obs


async def test_edit_by_command_await_returns_tool_result_with_usage(store, make_llm):
    """Task 8 迁移：直接 await 模块级协程得 ToolResult——观测文本与 registry 路径一致、
    usage 非 None（入账路径：loop 对 ToolResult.usage 同账累加，P1 预算缺口修复）。"""
    edits = json.dumps([{"path": "characters/陆昭.md",
                         "content": _alias_card(["昭哥", "昭公子"])}], ensure_ascii=False)
    llm, calls = make_llm([edits])
    ui = FakeUI(choice=Choice(indices=[0]))
    result = await edit_by_command(store, llm, PromptLoader(home_dir=store.root), ui,
                                   {"instruction": "给陆昭加别名昭公子"})
    assert isinstance(result, ToolResult)
    assert "已应用 1 个文件" in result.text
    assert result.usage is not None
    assert result.usage.prompt_tokens == 3 and result.usage.completion_tokens == 7
    assert len(calls) == 1
    assert "昭公子" in store.read_character("陆昭")["aliases"]


# ---------------------------------------------------------------- Task 8 行为等价钉死
# 迁移（内联阶梯 → base.complete_structured）后失败观测须与迁移前逐字节一致：
# detail（finish_reason/原始消息）经 _LastErrorProbe 从被吞的 LLMError 取回。

def test_edit_by_command_starved_failure_observation_unchanged(store, make_llm):
    starved = {"content": "", "finish": "length"}   # 空内容 + finish=length → kind=starved
    llm, calls = make_llm([starved, starved])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "改东西"},
                      allowed=frozenset({"edit_by_command"}))
    assert obs == "edit_by_command 失败（truncated）：finish_reason=length"
    assert len(calls) == 2                       # starved 扩额恰好重试一次（×2 封顶）
    assert calls[1]["max_tokens"] == min(CAP_MAX_TOKENS, calls[0]["max_tokens"] * 2)
    assert ui.asked == []                        # 提议失败不开确认门


def test_edit_by_command_empty_failure_observation_unchanged(store, make_llm):
    empty = {"content": "", "finish": "stop"}     # 空内容 + finish=stop → kind=empty
    llm, calls = make_llm([empty, empty])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "改东西"},
                      allowed=frozenset({"edit_by_command"}))
    assert obs == "edit_by_command 失败（empty）：finish_reason=stop"
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == CAP_MAX_TOKENS  # empty 直接顶 cap


def test_edit_by_command_bad_json_failure_observation_unchanged(store, make_llm):
    llm, calls = make_llm(["这不是 JSON", "{{{"])
    ui = FakeUI(choice=Choice(indices=[0]))
    reg = make_registry(store, llm, ui)
    obs = reg.execute("edit_by_command", {"instruction": "改东西"},
                      allowed=frozenset({"edit_by_command"}))
    assert obs == ("edit_by_command 失败（bad_json）：两次调用均未能产出修改提议——"
                   "响应中解析不出 [{path, content}] JSON")
    assert len(calls) == 2                       # 解析失败扩额恰好重试一次（×2 封顶）


# ---------------------------------------------------------------- resequence_chapters（P4 Task 8 联动）

def _resequence_setup(store):
    """两章乱序：seq1 属情节 1-2、seq2 属情节 1-1（重排后应互换章号）。"""
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    store.upsert_plots(1, [{"seq": 1, "title": "开端", "status": "planned"},
                           {"seq": 2, "title": "叛变", "status": "planned"}])
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "叛变",
                                                "status": "done", "plot_id": "1-2"},
                                   body="叛变正文"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "开端",
                                                "status": "done", "plot_id": "1-1"},
                                   body="开端正文"))
    mem = MemoryStore(store.root)
    mem.write_chapter_memory(1, "叛变", {
        "events": [{"title": "刀光现", "summary": "叛变者拔刀", "location": "大殿"}],
        "promise_updates": [{"kind": "foreshadow", "content": "叛变者的刀",
                             "by_character": "陆昭", "status": "open"}]})
    mem.write_chapter_memory(2, "开端", {
        "events": [{"title": "夜雨行", "summary": "主角进山", "location": "山道"}],
        "promise_updates": [{"kind": "foreshadow", "content": "青铜钥匙",
                             "by_character": "陆昭", "status": "open"}]})
    mem.apply_state_updates({"characters": [
        {"name": "陆昭", "notes": "在第2章拿到图谱"}]}, 2)
    return mem


def test_resequence_renames_memory_files(store):
    """重排后：章文件、记忆文件名与「第N章」锚点、state.md 数值全部重映射。"""
    from harness.tools.modify import resequence_chapters

    _resequence_setup(store)
    out = resequence_chapters(store, {})
    assert "重排完成" in out

    docs = store.list_chapters()
    assert [d.seq for d in docs] == [1, 2]
    assert docs[0].meta["title"] == "开端" and docs[1].meta["title"] == "叛变"

    mem = MemoryStore(store.root)
    assert mem.list_chapter_memories() == [1, 2]
    text1 = (store.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert text1.startswith("# 第1章记忆《开端》")   # 原第2章记忆整体落到新第1章
    assert "夜雨行" in text1 and "刀光现" not in text1
    text2 = (store.root / "memory" / "0002.md").read_text(encoding="utf-8")
    assert text2.startswith("# 第2章记忆《叛变》")
    state = (store.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert "updated: 第1章" in state and "updated: 第2章" not in state
    # 注：notes 散文里的「第N章」不在 _remap_state_seq 口径内（T4 seed 对拍，散文不重映射）


def test_resequence_memory_failure_noted(store, monkeypatch):
    """记忆重命名失败不阻断重排，观测如实注记（裁定 8）。"""
    from harness.tools.modify import resequence_chapters

    _resequence_setup(store)
    calls = []

    def boom(self, old, new):
        calls.append((old, new))
        raise RuntimeError("disk full")

    monkeypatch.setattr(MemoryStore, "rename_chapter_seq", boom)
    out = resequence_chapters(store, {})
    assert calls                        # 联动确实被调用（逐个 old→new）
    assert "记忆联动未完成" in out
    assert "失败（" not in out          # M-a：非阻断注记不得含「失败（」防 _classify 误判
    docs = store.list_chapters()        # 章节重排不受记忆失败阻断
    assert [d.seq for d in docs] == [1, 2]
    assert docs[0].meta["title"] == "开端"


# ---------------------------------------------------------------- resequence 健壮性（fix 轮 1/5）

def _transient_markers():
    from harness.agent.loop import _EXTRA_TRANSIENT_MARKERS
    from harness.run import constants
    return constants.TRANSIENT_MARKERS + _EXTRA_TRANSIENT_MARKERS


def test_resequence_chapter_write_failure_noted(store, monkeypatch):
    """单章 write_chapter 抛错 → 其余章照常重排、观测含失败注记（章号+类型名）、
    零瞬时词；该章下一趟写入成功，整体仍收敛（记忆联动不打折）。"""
    from harness.tools.modify import resequence_chapters

    _resequence_setup(store)
    real = ProjectStore.write_chapter
    fired = {"n": 0}

    def flaky(self, doc):
        if str(doc.meta.get("title") or "") == "叛变":
            fired["n"] += 1
            if fired["n"] == 1:                 # 只炸第一趟（中转趟），第二趟自愈
                raise RuntimeError("disk full")
        return real(self, doc)

    monkeypatch.setattr(ProjectStore, "write_chapter", flaky)
    out = resequence_chapters(store, {})
    assert fired["n"] == 2                      # 两趟都走到该章；第一趟炸、第二趟过
    assert "文件写入未完成" in out and "RuntimeError" in out
    assert "失败（" not in out          # M-a：非阻断注记不得含「失败（」防 _classify 误判
    for marker in _transient_markers():
        assert marker not in out, f"观测泄漏瞬时词 {marker!r}：{out!r}"
    docs = store.list_chapters()                # 其余章照常重排，终序收敛
    assert [d.seq for d in docs] == [1, 2]
    assert docs[0].meta["title"] == "开端" and docs[1].meta["title"] == "叛变"
    mem = MemoryStore(store.root)               # 记忆联动照常收敛
    assert mem.list_chapter_memories() == [1, 2]
    assert (store.root / "memory" / "0001.md").read_text(encoding="utf-8") \
        .startswith("# 第1章记忆《开端》")


def test_resequence_transit_self_heal(store, monkeypatch):
    """记忆 rename 全程失败（章已收敛、记忆停在旧号且与章错位——两记忆互换位置的
    成环态）→ 重跑 resequence → 自愈趟经 transit 号消化残留，收敛到最终序
    （文件名/「第N章」锚点/state.md 数值终态正确——记忆按章标题定位找回）。"""
    from harness.tools.modify import resequence_chapters

    _resequence_setup(store)
    real = MemoryStore.rename_chapter_seq
    state = {"fail": True}

    def flaky(self, old, new):
        if state["fail"]:
            raise RuntimeError("boom")
        return real(self, old, new)

    monkeypatch.setattr(MemoryStore, "rename_chapter_seq", flaky)
    out1 = resequence_chapters(store, {})
    assert "记忆" in out1 and "未完成" in out1
    assert "失败（" not in out1          # M-a：非阻断注记不得含「失败（」防 _classify 误判
    assert [d.seq for d in store.list_chapters()] == [1, 2]   # 章侧已收敛
    # 残留态：记忆文件停在旧号且与章错位（0001=叛变、0002=开端——互换成环）
    assert (store.root / "memory" / "0001.md").read_text(encoding="utf-8")         .startswith("# 第1章记忆《叛变》")

    state["fail"] = False                       # 重跑（真实 rename）→ 自愈
    out2 = resequence_chapters(store, {})
    assert "记忆中转残留已自愈" in out2     # 自愈如实观测
    assert [d.seq for d in store.list_chapters()] == [1, 2]
    names = sorted(p.name for p in (store.root / "memory").glob("*.md"))
    assert names == ["0001.md", "0002.md", "state.md"]      # transit 号消化
    t1 = (store.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert t1.startswith("# 第1章记忆《开端》")
    assert "第1章埋" in t1 and "第2章埋" not in t1             # 记忆锚点终态
    t2 = (store.root / "memory" / "0002.md").read_text(encoding="utf-8")
    assert t2.startswith("# 第2章记忆《叛变》") and "第2章埋" in t2
    state_md = (store.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert "updated: 第1章" in state_md


# ------------------------------------------------------------------ 现实参照预检（P5 T10）

class _FakeRefService:
    """假 WebSearchService：verify 直接给结论（真服务在 tests/test_websearch.py 覆盖）。"""

    instances: ClassVar[list["_FakeRefService"]] = []

    def __init__(self, settings, llm, prompts, **kwargs):
        self.usage_total = None
        self.closed = False
        _FakeRefService.instances.append(self)

    async def verify(self, query, kind="fact"):
        from harness.websearch import ResearchEntry, query_key_of

        return ResearchEntry(query=query, query_key=query_key_of(query), kind=kind,
                             conclusion=f"已核实：{query}", sources=["https://x.test/1"],
                             hit_count=1)

    async def aclose(self):
        self.closed = True


def _ref_settings(*, web_verify=True, with_search=True):
    from pathlib import Path

    from harness.config import SearchSettings, Settings

    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=web_verify,
        search=(SearchSettings(provider="kimi_api", base_url="https://api.test", api_key="k")
                if with_search else None),
    )


def test_ref_check_hooks_world_edit(store, make_llm, monkeypatch):
    """世界观目标 → 预检跑起来：propose 提示词含【已核实参照】、research.md 落账、
    real_ref_checked 事件发出。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    refs = json.dumps({"refs": [{"name": "率土之滨", "query": "率土之滨 内政 机制"},
                                 {"name": "文明6", "query": "文明6 尤里卡 触发"}]},
                      ensure_ascii=False)
    llm, calls = make_llm([refs, json.dumps(
        [{"path": "world.md", "content": "---\nentries: []\n---\n\n"}], ensure_ascii=False)])
    events = []

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "新增一条地理设定到世界观"},
                                emit=lambda kind, payload: events.append((kind, payload)),
                                settings=_ref_settings()))

    # 预检的提示词先发，propose 的提示词后发
    assert len(calls) == 2
    propose_prompt = calls[1]["messages"][0]["content"]
    assert "【已核实参照】" in propose_prompt
    assert "已核实：率土之滨 内政 机制" in propose_prompt
    assert "已核实：文明6 尤里卡 触发" in propose_prompt
    # research.md 落账
    research = (store.root / ".harness" / "research.md").read_text(encoding="utf-8")
    assert "率土之滨 内政 机制" in research
    # 事件
    assert ("real_ref_checked", {"count": 2}) in events


class _FakeNoSourceService:
    """假服务：verify 给出结论但**无检索结果支撑**（sources 为空 → 视为未核实）。"""

    instances: ClassVar[list["_FakeNoSourceService"]] = []

    def __init__(self, settings, llm, prompts, **kwargs):
        self.usage_total = None
        self.closed = False
        _FakeNoSourceService.instances.append(self)

    async def verify(self, query, kind="fact"):
        from harness.websearch import ResearchEntry, query_key_of

        return ResearchEntry(query=query, query_key=query_key_of(query), kind=kind,
                             conclusion=f"凭记忆编的：{query}", sources=[], hit_count=1)

    async def aclose(self):
        self.closed = True


def test_ref_check_skips_entry_without_sources(store, make_llm, monkeypatch):
    """核实没有检索结果支撑（sources 空）→ 不产【已核实参照】块（无出处的结论不算已核实）。"""
    from harness.tools.modify import edit_by_command

    _FakeNoSourceService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeNoSourceService)
    llm, calls = make_llm([json.dumps(
        [{"path": "world.md", "content": "---\nentries: []\n---\n\n"}], ensure_ascii=False)])

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "新增一条地理设定到世界观"},
                                settings=_ref_settings()))

    assert len(calls) == 2                       # 预检列 refs 一次 + propose 一次
    propose_prompt = calls[1]["messages"][0]["content"]
    assert "【已核实参照】" not in propose_prompt
    assert "凭记忆编的" not in propose_prompt


def test_ref_check_skipped_when_disabled(store, make_llm, monkeypatch):
    """web_verify=false 或未配 [search] → 不预检（零 LLM 调用额外开销）。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    llm, calls = make_llm([json.dumps(
        [{"path": "world.md", "content": "---\nentries: []\n---\n\n"}], ensure_ascii=False)])

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "新增一条地理设定到世界观"},
                                settings=_ref_settings(web_verify=False)))

    assert len(calls) == 1                      # 只有 propose 一次
    assert "【已核实参照】" not in calls[0]["messages"][0]["content"]
    assert _FakeRefService.instances == []


def test_ref_check_skipped_when_no_search_section(store, make_llm, monkeypatch):
    """未配 [search] → 不预检。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    llm, calls = make_llm([json.dumps(
        [{"path": "world.md", "content": "---\nentries: []\n---\n\n"}], ensure_ascii=False)])

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "新增一条地理设定到世界观"},
                                settings=_ref_settings(with_search=False)))

    assert len(calls) == 1
    assert _FakeRefService.instances == []


def test_ref_check_skipped_for_non_world(store, make_llm, monkeypatch):
    """非世界观目标（角色卡）→ 不预检。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    llm, calls = make_llm([json.dumps(
        [{"path": "characters/陆昭.md", "content": "---\nname: 陆昭\n---\n\n"}],
        ensure_ascii=False)])

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "把陆昭的说话风格改得更冷"},
                                settings=_ref_settings()))

    assert len(calls) == 1
    assert _FakeRefService.instances == []


def test_ref_check_fallback_refs(store, make_llm, monkeypatch):
    """模型列不出 refs → 确定性兜底（《书名》/常见作品/题材正典）非空。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    llm, calls = make_llm([json.dumps({"refs": []}, ensure_ascii=False),
                           json.dumps([{"path": "world.md",
                                        "content": "---\nentries: []\n---\n\n"}],
                                      ensure_ascii=False)])

    import asyncio
    asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                FakeUI(choice=Choice(indices=[0])),
                                {"instruction": "给世界观补一条《魔戒》式的设定"},
                                settings=_ref_settings()))

    assert len(calls) == 2
    assert "【已核实参照】" in calls[1]["messages"][0]["content"]
    assert "魔戒" in calls[1]["messages"][0]["content"]


def test_ref_check_failure_nonblocking(store, make_llm, monkeypatch):
    """预检整体失败（LLM 抛错）→ 修改路径照常完成，观测不含失败（不阻断）。"""
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)
    llm, calls = make_llm([RuntimeError("boom"),
                           json.dumps([{"path": "world.md",
                                        "content": "---\nentries: []\n---\n\n"}],
                                      ensure_ascii=False)])

    import asyncio
    result = asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                         FakeUI(choice=Choice(indices=[0])),
                                         {"instruction": "新增一条地理设定到世界观"},
                                         settings=_ref_settings()))

    assert len(calls) == 2                     # 预检失败后 propose 照发
    assert "【已核实参照】" not in calls[1]["messages"][0]["content"]
    assert "失败（" not in result.text          # 不阻断、非阻断注记不得含「失败（」


def test_ref_check_broken_world_does_not_break_edit(store, make_llm, monkeypatch):
    """读不出 world 条目 → 预检降级跳过，**不崩整条修改路径**（回归防线）。

    预检要读现有 world 条目标题；读不出来时必须吞掉（预检是增强步骤，不得让
    edit_by_command 整条路径崩掉——改前它只读原始文本，坏档走逐文件降级）。
    """
    from harness.store.frontmatter import FileFormatError
    from harness.tools.modify import edit_by_command

    _FakeRefService.instances = []
    monkeypatch.setattr("harness.websearch.WebSearchService", _FakeRefService)

    def _boom(self):
        raise FileFormatError(self.root / "world.md", "front-matter 解析失败: 模拟坏档")

    monkeypatch.setattr(ProjectStore, "read_world", _boom)
    llm, calls = make_llm([json.dumps(
        [{"path": "world.md", "content": "---\nentries: []\n---\n\n"}], ensure_ascii=False)])

    import asyncio
    result = asyncio.run(edit_by_command(store, llm, PromptLoader(home_dir=store.root),
                                         FakeUI(choice=Choice(indices=[0])),
                                         {"instruction": "新增一条地理设定到世界观"},
                                         settings=_ref_settings()))

    assert len(calls) == 1                      # 预检跳过，只发 propose
    assert _FakeRefService.instances == []
    assert "失败（" not in result.text          # 非阻断
