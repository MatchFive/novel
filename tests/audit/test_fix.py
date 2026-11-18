# 审计 issue 的「交给 AI 修」（P5 Task 5）
"""fix_issue 语义钉死：

- quote 定位：issue.chapter 有值先查该章，查不到再全章扫；定位不到 → 如实说明且
  **issue 保持 pending**（零 LLM 调用、零写盘）；
- 修复调用：task.consistency_fix（路由 standard/0.3/2048）→ JSON {"replacement": str}；
  解析失败 / 无 replacement / 替换文本与原文一致 → 观测如实、issue 保持 pending、不炸；
- diff 确认卡：确认前零写盘；取消（或审批抛 ApprovalCancelled）→ 保持 pending；
- 落盘：确认后只替换 quote 定位到的那一段（其余正文逐字不动）→ 章正文写盘 →
  记忆重提取（reextract_chapter，幂等先清后建）→ issue resolved；
- 观测文本逐字口径（首行/全文）。
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.audit.fix import _rewrite_in_place, fix_issue
from harness.config import ProviderConfig, Settings
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.approval import ApprovalCancelled, ApprovalUI, Choice
from harness.run.issues import IssuesStore
from harness.store.project import ChapterDoc, ProjectStore

BODY = "甲拔刀，乙点火。夜风吹过山寨。"
QUOTE = "夜风吹过山寨"
NEW_TEXT = "晨风掠过山寨"


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """顺序吐脚本的假 llm：complete 消费脚本项（形态遵 P3 纪律：complete 返回 JSON body）。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(
            base_url="https://api.test", api_key="k",
            lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=8192):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m-lite", standard="m-std", pro="m-pro")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def prompts_for(root: Path) -> PromptLoader:
    return PromptLoader(project_root=root, home_dir=root)


def extract_payload() -> str:
    """五键提取 fixture（角色名不入名册 → 角色经历被过滤，事件条目仍落盘）。"""
    return json.dumps({
        "events": [{"title": "夜探山寨", "summary": "甲潜入山寨", "location": "山寨",
                    "time_marker": "深夜", "characters": ["甲"],
                    "new_info": ["布防图"], "foreshadow": "后山密道"}],
        "character_events": [], "info_updates": [], "promise_updates": [],
        "state_updates": {"characters": [], "world": []},
    }, ensure_ascii=False)


def fix_payload(replacement: str = NEW_TEXT) -> str:
    return json.dumps({"replacement": replacement}, ensure_ascii=False)


def _project(tmp_path, *, chapters: int = 1):
    info = create_project(tmp_path, "修复书")
    store = ProjectStore(info.path)
    for seq in range(1, chapters + 1):
        body = BODY if seq == 1 else f"第{seq}章：乙独自赶路。"
        store.write_chapter(ChapterDoc(seq=seq, meta={"seq": seq, "title": f"第{seq}章",
                                                      "status": "done"}, body=body))
    mem = MemoryStore(info.path)
    issues = IssuesStore(info.path)
    return info, store, mem, issues


def _ui(indices=(0,), raiser=None):
    """假确认门：记录问题文本，返回指定选项；raiser 非 None 时抛（审批取消）。"""
    seen: list[str] = []

    def ask(question, options, multi=False, note=None):
        seen.append(question)
        if raiser is not None:
            raise raiser
        return Choice(indices=list(indices))

    return ApprovalUI(ask=ask), seen


async def test_fix_happy_path(tmp_path):
    """确认 → 该段被替换（其余正文不动）→ 章落盘 → 记忆重提取 → issue resolved。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "时间锚点错位", "第 1 章提到黎明但发生在夜里",
                       QUOTE, chapter=1)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue,
                           emit=lambda kind, payload: None, ui=ui)

    assert text == "已修改第1章并标记 resolved，记忆已重提取"
    body = store.read_chapter(1).body
    assert NEW_TEXT in body
    assert QUOTE not in body
    assert body.startswith("甲拔刀，乙点火。")      # quote 之外逐字不动
    assert issues.list("pending") == []
    assert issues.list("resolved")[0].id == issue.id
    # 重提取生效：章记忆落盘
    assert (mem.root / "memory" / "0001.md").exists()
    # diff 预览进确认卡
    assert QUOTE in seen[0] and NEW_TEXT in seen[0]


async def test_fix_quote_not_found_stays_pending(tmp_path):
    """quote 定位不到 → 如实说明 + issue 保持 pending；零 LLM 调用、零写盘。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", "正文里根本不存在的片段", chapter=1)
    llm = FakeLLM([])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text == "quote 已不可定位，issue 保持 pending"
    assert issues.list("pending")[0].id == issue.id
    assert llm.calls == []
    assert store.read_chapter(1).body == BODY


async def test_fix_user_cancels(tmp_path):
    """确认卡选「取消」→ 零写盘、issue 保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=1)
    llm = FakeLLM([fix_payload()])
    ui, _seen = _ui(indices=(1,))

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text == "已取消，issue 保持 pending"
    assert store.read_chapter(1).body == BODY
    assert issues.list("pending")[0].id == issue.id


async def test_fix_approval_cancelled(tmp_path):
    """审批抛 ApprovalCancelled → 同「取消」：零写盘、保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=1)
    llm = FakeLLM([fix_payload()])
    ui, _seen = _ui(raiser=ApprovalCancelled())

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text == "已取消，issue 保持 pending"
    assert store.read_chapter(1).body == BODY
    assert issues.list("pending")[0].id == issue.id


async def test_fix_bad_json_stays_pending(tmp_path):
    """响应非 JSON → 两轮阶梯后失败：观测如实、issue 保持 pending、不炸。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=1)
    llm = FakeLLM(["完全不是 JSON", "依然不是 JSON"])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text.startswith("修复失败（")
    assert "issue 保持 pending" in text
    assert store.read_chapter(1).body == BODY
    assert issues.list("pending")[0].id == issue.id


async def test_fix_missing_replacement_key_stays_pending(tmp_path):
    """响应是 JSON 但无 replacement → 同解析失败口径（不把空替换写进正文）。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=1)
    llm = FakeLLM([json.dumps({"foo": 1}), json.dumps({"foo": 1})])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text.startswith("修复失败（")
    assert store.read_chapter(1).body == BODY
    assert issues.list("pending")[0].id == issue.id


async def test_fix_no_change_stays_pending(tmp_path):
    """替换文本与原文一致 → 「修复无变化」，不写盘、保持 pending。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=1)
    llm = FakeLLM([fix_payload(QUOTE)])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text == "修复无变化：替换文本与原文一致，issue 保持 pending"
    assert store.read_chapter(1).body == BODY
    assert issues.list("pending")[0].id == issue.id


async def test_fix_locates_quote_in_other_chapter(tmp_path):
    """issue 记录的章号与 quote 实际落点漂移 → 回退全章扫，命中即改那一章。"""
    _info, store, mem, issues = _project(tmp_path, chapters=2)
    issue = issues.add("audit", "标题", "详情", QUOTE, chapter=2)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text.startswith("已修改第1章")
    assert NEW_TEXT in store.read_chapter(1).body
    assert store.read_chapter(2).body == "第2章：乙独自赶路。"


async def test_fix_prompt_carries_four_vars(tmp_path):
    """渲染四变量同名传入（问题标题/描述、待替换原文、上下文进提示词）。"""
    _info, store, mem, issues = _project(tmp_path)
    issue = issues.add("audit", "时间锚点错位", "第 1 章提到黎明但发生在夜里",
                       QUOTE, chapter=1)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, _seen = _ui()

    await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(store.root),
                    settings=make_settings(), issue=issue, ui=ui)

    prompt = llm.calls[0]["prompt"]
    assert "时间锚点错位" in prompt
    assert "第 1 章提到黎明但发生在夜里" in prompt
    assert QUOTE in prompt
    assert "甲拔刀" in prompt          # 上下文（quote ±500 字）进提示词
    assert "{{" not in prompt


# ------------------------------------------------------------------ 就地改写（I-1）

#: 手写章的 front-matter（**带用户注释**——重序列化会把注释吃掉）
HAND_FRONT = ("---\r\n"
              "# 手写注释：这一行是用户自己加的，不能丢\r\n"
              "seq: 1\r\n"
              "title: 手写章标题\r\n"
              "status: done\r\n"
              "---\r\n"
              "\r\n")
#: 手写章正文（CRLF 行尾）
HAND_BODY = "甲拔刀，乙点火。\r\n夜风吹过山寨。\r\n丙沉默。\r\n"
#: 改写后应得的**逐字节**结果：只有定位到的那一段变了
HAND_EXPECTED = (HAND_FRONT + "甲拔刀，乙点火。\r\n晨风掠过山寨。\r\n丙沉默。\r\n").encode("utf-8")


async def test_fix_rewrites_handwritten_chapter_in_place(tmp_path):
    """手写章（文件名与 title 不一致 + front-matter 带注释 + CRLF 正文）→ **就地改写**：

    文件名不变、front-matter 原文逐字节不变、除定位段外正文逐字节不变、原文件不被删。
    （走 write_chapter 会按 title 重命名文件、删原文件、重新序列化 front-matter、
    裁正文首尾空白——对手写章是破坏性的。）
    """
    info = create_project(tmp_path, "手写书")
    store = ProjectStore(info.path)
    chapters = info.path / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    path = chapters / "0001-手写章.md"          # 文件名与 front-matter 里的 title 不一致
    path.write_bytes((HAND_FRONT + HAND_BODY).encode("utf-8"))
    mem = MemoryStore(info.path)
    issues = IssuesStore(info.path)
    issue = issues.add("audit", "时间锚点错位", "夜里却写黎明", QUOTE, chapter=1)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, _seen = _ui()

    text = await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(info.path),
                           settings=make_settings(), issue=issue, ui=ui)

    assert text.startswith("已修改第1章")
    # 文件名不变、原文件没被删、没多出第二份
    assert sorted(p.name for p in chapters.glob("*.md")) == ["0001-手写章.md"]
    # 逐字节：front-matter 原文（含注释）与 CRLF 行尾都在，只有定位段被换
    assert path.read_bytes() == HAND_EXPECTED
    # front-matter 区域逐字节不变
    assert path.read_bytes().startswith(HAND_FRONT.encode("utf-8"))
    assert issues.list("pending") == []
    assert issues.list("resolved")[0].id == issue.id


async def test_fix_rewrites_the_file_where_quote_was_found(tmp_path):
    """front-matter 的 seq 与**文件名前缀不一致** → 改写必须落在**含 quote 的那个文件**。

    复现：``0001-甲.md`` 的 front-matter 写 ``seq: 2``、``0002-乙.md`` 写 ``seq: 1``，
    两章正文相同。按 seq 再解析一次路径（``chapter_file_path`` 按文件名前缀 glob）
    → 替换文本写进 ``0002-乙.md``，``0001-甲.md`` 纹丝不动（改错了章）。
    """
    info = create_project(tmp_path, "错位书")
    store = ProjectStore(info.path)
    chapters = info.path / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    front_jia = "---\nseq: 2\ntitle: 甲\nstatus: done\n---\n\n"
    front_yi = "---\nseq: 1\ntitle: 乙\nstatus: done\n---\n\n"
    path_jia = chapters / "0001-甲.md"
    path_yi = chapters / "0002-乙.md"
    path_jia.write_text(front_jia + BODY + "\n", encoding="utf-8")
    path_yi.write_text(front_yi + BODY + "\n", encoding="utf-8")
    mem = MemoryStore(info.path)
    issues = IssuesStore(info.path)
    issue = issues.add("audit", "时间锚点错位", "夜里却写黎明", QUOTE, chapter=2)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, _seen = _ui()

    await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(info.path),
                    settings=make_settings(), issue=issue, ui=ui)

    # 改写落在**定位到 quote 的那个文件**（seq=2 → 0001-甲.md）
    assert path_jia.read_text(encoding="utf-8") == front_jia + "甲拔刀，乙点火。晨风掠过山寨。\n"
    # 另一个文件逐字节不变
    assert path_yi.read_text(encoding="utf-8") == front_yi + BODY + "\n"


# ------------------------------------------------------------------ 记忆重提取按定位章（P5 复审遗留 1）

async def test_fix_refreshes_memory_of_the_rewritten_chapter(tmp_path):
    """改写甲 → **甲的记忆**被刷新（提取的是**改后**的正文），
    **乙的记忆逐字节不变**（不被清掉重写）。

    复现：``0001-甲.md`` 的 front-matter 写 ``seq: 2``、``0002-乙.md`` 写 ``seq: 1``，
    两章正文相同。记忆重提取若再按 ``seq=2`` glob 一次 → 读到 ``0002-乙.md``
    → **乙的记忆被清掉重写、甲的记忆从不刷新**。
    """
    info = create_project(tmp_path, "记忆错位书")
    store = ProjectStore(info.path)
    chapters = info.path / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    front_jia = "---\nseq: 2\ntitle: 甲\nstatus: done\n---\n\n"
    front_yi = "---\nseq: 1\ntitle: 乙\nstatus: done\n---\n\n"
    (chapters / "0001-甲.md").write_text(front_jia + BODY + "\n", encoding="utf-8")
    (chapters / "0002-乙.md").write_text(front_yi + BODY + "\n", encoding="utf-8")
    mem = MemoryStore(info.path)
    mem.write_chapter_memory(2, "甲", {"events": [{"title": "甲的旧记忆", "summary": "旧",
                                                    "location": "", "time_marker": "",
                                                    "characters": []}]})
    mem.write_chapter_memory(1, "乙", {"events": [{"title": "乙的旧记忆", "summary": "旧",
                                                    "location": "", "time_marker": "",
                                                    "characters": []}]})
    yi_before = (info.path / "memory" / "0001.md").read_bytes()
    issues = IssuesStore(info.path)
    issue = issues.add("audit", "时间锚点错位", "夜里却写黎明", QUOTE, chapter=2)
    llm = FakeLLM([fix_payload(), extract_payload()])
    ui, _seen = _ui()

    await fix_issue(store=store, mem=mem, llm=llm, prompts=prompts_for(info.path),
                    settings=make_settings(), issue=issue, ui=ui)

    # 甲的记忆被刷新：提取提示词里是**改后**的正文（不是盘上另一章的旧正文）
    assert NEW_TEXT in llm.calls[1]["prompt"]
    jia_mem = (info.path / "memory" / "0002.md").read_text(encoding="utf-8")
    assert "夜探山寨" in jia_mem          # 新提取的条目
    assert "甲的旧记忆" not in jia_mem    # 旧条目已清掉（先清后建）
    # 乙的记忆逐字节不变
    assert (info.path / "memory" / "0001.md").read_bytes() == yi_before


# ------------------------------------------------------------------ 回退路径失败关闭（P5 复审遗留 2）

def _mismatched_project(tmp_path):
    """造「手写章」：``0001-甲.md``(fm seq:2) + ``0002-乙.md``(fm seq:1)，两章正文相同。"""
    info = create_project(tmp_path, "回退书")
    store = ProjectStore(info.path)
    chapters = info.path / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    (chapters / "0001-甲.md").write_text("---\nseq: 2\ntitle: 甲\nstatus: done\n---\n\n"
                                         + BODY + "\n", encoding="utf-8")
    (chapters / "0002-乙.md").write_text("---\nseq: 1\ntitle: 乙\nstatus: done\n---\n\n"
                                         + BODY + "\n", encoding="utf-8")
    return info, store


def test_rewrite_in_place_without_path_refuses_on_body_mismatch(tmp_path):
    """``doc.path`` 为 None（手工构造的章）→ 回退路径**失败关闭**：
    盘上正文与 ``doc.body`` 对不上 → 拒绝改写（一个文件都不动）。"""
    info, store = _mismatched_project(tmp_path)
    doc = ChapterDoc(seq=1, meta={"seq": 1, "title": "手写"}, body="完全不同的正文")

    assert _rewrite_in_place(store, doc, 0, len(doc.body), "新文本") is False
    assert (info.path / "chapters" / "0001-甲.md").read_text(encoding="utf-8") \
        == "---\nseq: 2\ntitle: 甲\nstatus: done\n---\n\n" + BODY + "\n"
    assert (info.path / "chapters" / "0002-乙.md").read_text(encoding="utf-8") \
        == "---\nseq: 1\ntitle: 乙\nstatus: done\n---\n\n" + BODY + "\n"


def test_rewrite_in_place_without_path_does_not_guess_another_chapter(tmp_path):
    """``doc.path`` 为 None + **别的章正文恰好相同** → 仍不得改那个文件（不猜着改）。

    glob 出来的是**文件名前缀**，``doc.seq`` 是内存里的章号——两者不一致时 glob 命中
    的是别的章；正文相同更是让「正文比对」这道闸失效 → 只能靠章号比对拒绝。
    """
    info, store = _mismatched_project(tmp_path)
    before = (info.path / "chapters" / "0002-乙.md").read_bytes()
    doc = ChapterDoc(seq=2, meta={"seq": 2, "title": "甲"}, body=BODY)

    assert _rewrite_in_place(store, doc, 0, len(BODY), "晨风掠过山寨") is False
    assert (info.path / "chapters" / "0002-乙.md").read_bytes() == before
    assert (info.path / "chapters" / "0001-甲.md").read_text(encoding="utf-8") \
        == "---\nseq: 2\ntitle: 甲\nstatus: done\n---\n\n" + BODY + "\n"
