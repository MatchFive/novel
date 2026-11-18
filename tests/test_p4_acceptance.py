"""P4 验收三场景（LLM 全 mock：JSON-body / SSE 网关同 tests/test_p3_acceptance.py，
_sse_body 复用 tests/tools/test_base.py）。全链真实 ProjectStore + MemoryStore +
EventLog（events.log 实文件）+ Index（write_plot 内 build_corpus），对照设计文档
P4 验收行（local-harness-design.md §3）：

1. **重复提取幂等**（验收①）：write_plot 写实章 → 记忆提取落盘 → 改正文重提取
   （生产入口 extract_chapter_memory，与链上 _extract_memory 同函数）→ 记忆文件
   先清后建无重复条目、state.md 无重复行且只随新提取前进（单调）。
2. **跨章可见 + 时间锚点纪律**（验收②）：写 2 章，第 2 章 draft 提示词含第 1 章
   POV 经历（[第1章] 锚点）/POV 卡【当前状态】行/【世界现状】块/【未回收承诺】提醒；
   反向断言——预置的「未来第 3 章记忆」与第 2 章自身（草稿时尚未提取）一律不得出现。
3. **回定位**（验收③）：「之前写过某物件/台词」经 search_chapters 工具层与
   write_plot【伏笔回指】块两个口径定位到章号 + 逐字引文（locate_quote 回验）。

另加零 LLM 判别（test_flavor_reproducible 同款）：同一记忆库
build_memory_bundle 两次，五块文本逐字一致。
"""
import json
import re
from pathlib import Path

import httpx
from tools.test_base import _sse_body

from harness.config import ProviderConfig, Settings
from harness.llm.client import OpenAICompatClient
from harness.memory.extract import extract_chapter_memory
from harness.memory.inject import build_memory_bundle
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.retrieval.corpus import Index, build_corpus
from harness.run.events import EventLog
from harness.store.project import ChapterDoc, ProjectStore
from harness.text import locate_quote
from harness.tools.search import search_chapters
from harness.tools.writing import write_plot

PLOT = {"seq": 1, "title": "开端", "summary": "概要一", "conflict": "冲突一",
        "resolution": "收束一", "status": "planned"}

SELECT_OK = json.dumps({"world_titles": [], "character_names": [],
                        "keywords": [], "need_web": []}, ensure_ascii=False)
# 角色卡名单由 context_select 产物驱动（writing/test_context.py 同口径）：
# 场景 2 的 select 挑出甲乙两张卡，POV 卡才带【当前状态】行。
SELECT_CAST = json.dumps({"world_titles": [], "character_names": ["甲", "乙"],
                          "keywords": [], "need_web": []}, ensure_ascii=False)
REVIEW_PASS = json.dumps({"pass": True, "issues": []}, ensure_ascii=False)

CAST = [{"name": "甲", "role_type": "主角", "personality": "冷静", "ability": "刀法"},
        {"name": "乙", "role_type": "配角", "personality": "莽"}]

_HIT_LINE = re.compile(r"^第(\d+)章《(.+?)》：「(.+)」$")


def make_settings():
    return Settings(
        library_root=Path("x"),
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


def U(p=10, c=20):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def beat(event, weight=1):
    return {"pov": "甲", "location": "山道", "event": event, "weight": weight}


def chapter(seq, title, beat_list, target=3000, plot_id="1-1"):
    meta = {"seq": seq, "title": title, "objective": f"目标{seq}", "status": "planned",
            "beats": beat_list, "covers": f"覆盖{seq}", "time_span": "夜",
            "start_at": "山道｜夜", "end_at": "山道｜夜",
            "plot_id": plot_id, "target_words": target}
    return ChapterDoc(seq=seq, meta=meta, body="")


def build_store(tmp_path, docs, title="验收书"):
    info = create_project(tmp_path, title)
    store = ProjectStore(info.path)
    store.upsert_arcs([{"vol": 1, "title": "卷甲", "goal": "g", "summary": "s",
                        "conflict": "c", "resolution": "r"}])
    store.upsert_plots(1, [dict(PLOT)])
    for card in CAST:
        store.upsert_character(dict(card))
    for doc in docs:
        store.write_chapter(doc)
    return store


def make_llm(router):
    """router(body) -> str | (content, usage) | {"chunks","usage"?} | Exception；
    stream 请求走 SSE（复用 test_base._sse_body），非流式走 JSON body。"""
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        item = router(body)
        if isinstance(item, Exception):
            raise item
        if body.get("stream"):
            chunks = item["chunks"] if isinstance(item, dict) else [item]
            usage = item.get("usage") if isinstance(item, dict) else None
            return httpx.Response(200, content=_sse_body(*chunks, usage=usage),
                                  headers={"Content-Type": "text/event-stream"})
        content, usage = item if isinstance(item, tuple) else (item, None)
        payload = {"choices": [{"message": {"content": content},
                                "finish_reason": "stop"}]}
        if usage:
            payload["usage"] = usage
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.test")
    client = OpenAICompatClient(
        ProviderConfig(base_url="https://api.test", api_key="k",
                       lite="m", standard="m", pro="m"),
        http_client=http)
    return client, calls


def draft_chunks(seed, n_chars=1400):
    """非严格周期的长正文（逐段变编号）：repeat_loop_cut 要求 ≥24 字严格循环单元，
    变编号拼接不会命中熔断。约 1400 字/拍，两拍 ≈2800 字 ≥ 0.85×3000 护栏。"""
    chunks, total, k = [], 0, 1
    while total < n_chars:
        c = f"第{k}段{seed}沿着山道继续往前走，天色一点点暗下来，远处有鸟叫。"
        chunks.append(c)
        total += len(c)
        k += 1
    return chunks


async def run_write(store, llm, events, args=None):
    return await write_plot(store, llm, PromptLoader(home_dir=store.root),
                            make_settings(), events.append, args or {"plot_id": "1-1"})


def read_events(store):
    return [json.loads(line) for line in
            (store.root / ".harness" / "events.log").read_text(encoding="utf-8")
            .splitlines()]


# ---------------------------------------------------------------- 提取 payload 构造器

def _payload(events, cevents, infos, promises, state_chars, state_world) -> str:
    return json.dumps({"events": events, "character_events": cevents,
                       "info_updates": infos, "promise_updates": promises,
                       "state_updates": {"characters": state_chars,
                                         "world": state_world}},
                      ensure_ascii=False)


def extract_payload_ch1() -> str:
    """第 1 章提取：POV 经历夺镜 + 台词/得失 + 信息项 + 未回收承诺（due 3）+
    甲现状（筑基后期 Lv12/山寨）+ 世界现状（青石城 importance 4）。"""
    return _payload(
        events=[{"title": "夺镜", "summary": "甲夺得铜镜", "location": "山寨",
                 "time_marker": "深夜", "characters": ["甲"], "new_info": [],
                 "foreshadow": ""}],
        cevents=[{"character": "甲", "summary": "甲夺得铜镜", "quotes": "灯在我手",
                  "acted": "", "felt": "", "got_lost": "得铜镜", "secrets": ""}],
        infos=[{"title": "铜镜图谱", "content": "铜镜纹路图谱", "revealed_to": ["甲"]}],
        promises=[{"kind": "promise", "content": "铜镜来历必查", "by_character": "甲",
                   "status": "pending", "resolution": "", "due": 3}],
        state_chars=[{"name": "甲", "realm": "筑基后期", "level": 12,
                      "location": "山寨", "affiliation": "刀客", "status": "alive",
                      "notes": ""}],
        state_world=[{"kind": "territory", "key": "青石城", "label": "青石城",
                      "value": "二级镇 · 粮320石", "importance": 4}])


def extract_payload_ch1_rev() -> str:
    """改正文后的第二次提取（措辞全变）：验证先清后建——旧条目不残留、无重复，
    state 随新值前进（location 山寨→河边 / level 12→13 / 世界粮数升档）。"""
    return _payload(
        events=[{"title": "二次提取", "summary": "甲折返河边", "location": "河边",
                 "time_marker": "", "characters": ["甲"], "new_info": [],
                 "foreshadow": ""}],
        cevents=[{"character": "甲", "summary": "折返河边", "quotes": "走",
                  "acted": "", "felt": "", "got_lost": "失铜镜", "secrets": ""}],
        infos=[], promises=[],
        state_chars=[{"name": "甲", "realm": "筑基后期", "level": 13,
                      "location": "河边", "affiliation": "刀客", "status": "alive",
                      "notes": ""}],
        state_world=[{"kind": "territory", "key": "青石城", "label": "青石城",
                      "value": "三级镇 · 粮500石", "importance": 5}])


def extract_payload_ch2() -> str:
    """第 2 章提取：与第 1 章措辞全异——第 2 章草稿提示词里不得出现这些条目
    （时间锚点纪律反向断言：草稿时本章记忆尚未提取）。"""
    return _payload(
        events=[{"title": "毁镜", "summary": "甲毁掉铜镜", "location": "废墟",
                 "time_marker": "", "characters": ["甲"], "new_info": [],
                 "foreshadow": ""}],
        cevents=[{"character": "甲", "summary": "甲毁掉铜镜", "quotes": "了断",
                  "acted": "", "felt": "", "got_lost": "失铜镜", "secrets": ""}],
        infos=[], promises=[],
        state_chars=[{"name": "甲", "realm": "筑基后期", "level": 13,
                      "location": "废墟", "affiliation": "刀客", "status": "alive",
                      "notes": ""}],
        state_world=[])


def writing_router(extract_for, select=SELECT_OK):
    """逐拍流式（【任务】锁在场）+ select + review 全过；记忆提取按
    extract_for(prompt) 分章路由（提示词带【章节正文】，可按正文锚点区分）。"""

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【任务】" in prompt and "只写" in prompt   # 逐拍锁在场
            router.state["n"] += 1
            return {"chunks": draft_chunks(f"稿{router.state['n']}号"),
                    "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return select, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "你是小说记忆整理员" in prompt:                      # task.memory_extract（P4 挂点）
            return extract_for(prompt), U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    router.state = {"n": 0}
    return router


# ================================================================ 场景 1：重复提取幂等

async def test_reextract_idempotent_full_chain(tmp_path):
    """验收①：write_plot 写实章（全链记忆提取落盘）→ 改正文 → 经生产入口重提取
    （与链上 _extract_memory 同函数 extract_chapter_memory）→ 记忆文件先清后建
    （旧条目零残留、条目无重复）+ state.md 无重复行且随新提取单调前进。"""
    store = build_store(tmp_path, [chapter(1, "题一", [beat("一之起"), beat("一之承")])])
    events = EventLog(store.root)
    llm, calls = make_llm(writing_router(lambda prompt: extract_payload_ch1()))
    result = await run_write(store, llm, events)
    assert "成功 1 章" in result.text

    mem = MemoryStore(store.root)
    data = mem.read_chapter_memory(1)
    assert data and data["events"][0]["title"] == "夺镜"       # 链上提取已落盘
    state = mem.read_state()
    assert state["characters"]["甲"]["level"] == "12"
    assert state["characters"]["甲"]["updated"] == 1
    assert state["characters"]["甲"]["last_seen"] == 1
    assert ("territory", "青石城") in state["world"]
    # 事件：chapter_done 如实报记忆状态；digest（投影）已增量维护
    lifecycle = [e for e in read_events(store) if e["kind"] != "draft_progress"]
    assert [(e["kind"], e["payload"]["seq"]) for e in lifecycle] \
        == [("chapter_start", 1), ("chapter_done", 1)]
    assert lifecycle[-1]["payload"]["memory_status"] == "extracted"
    digest = (store.root / "memory" / "digests" / "vol-01.md").read_text(encoding="utf-8")
    assert "### 第1章《题一》" in digest and "夺镜" in digest
    assert len(calls) == 5          # select 1 + stream 2 + review 1 + 记忆提取 1（digest 小不压缩）

    # 改正文 + 重提取（生产入口：与链上 _extract_memory 同一函数）
    doc = store.read_chapter(1)
    doc.body += "（修订：甲折返河边。）"
    store.write_chapter(doc)

    def rev_router(body):
        prompt = body["messages"][-1]["content"]
        assert "你是小说记忆整理员" in prompt
        assert "修订：甲折返河边。" in prompt                  # 重提的是改正后的正文
        return extract_payload_ch1_rev(), U(2, 3)

    llm2, calls2 = make_llm(rev_router)
    status = await extract_chapter_memory(
        mem=mem, llm=llm2, prompts=PromptLoader(home_dir=store.root),
        settings=make_settings(), doc=store.read_chapter(1), cast_cards=CAST)
    assert status == "extracted"
    assert len(calls2) == 1                                     # 恰好一次提取调用

    # 幂等：先清后建——旧条目全清、新条目恰一份、头/节不重复
    text = (store.root / "memory" / "0001.md").read_text(encoding="utf-8")
    assert text.count("# 第1章记忆《题一》") == 1
    assert text.count("## 事件") == 1 and text.count("## 角色经历") == 1
    assert text.count("- [全知] ") == 1
    assert "二次提取" in text and "夜探山寨" not in text
    assert "折返河边" in text and "翻进寨墙" not in text
    assert "铜镜图谱" not in text and "铜镜来历必查" not in text   # info/promise 旧条目同清
    # state 单调：无重复行、随新提取前进（location 山寨→河边、level 12→13、粮数升档），
    # updated/last_seen 不双重记账
    state_text = (store.root / "memory" / "state.md").read_text(encoding="utf-8")
    assert state_text.count("- 甲｜") == 1
    assert "location: 河边" in state_text and "location: 山寨" not in state_text
    assert "level: 13" in state_text and "level: 12" not in state_text
    assert "三级镇 · 粮500石" in state_text and "二级镇" not in state_text
    assert state_text.count("- [territory] 青石城｜") == 1
    assert "updated: 第1章" in state_text and "last_seen: 第1章" in state_text
    state = mem.read_state()
    assert state["characters"]["甲"]["updated"] == 1
    assert state["characters"]["甲"]["last_seen"] == 1


# ================================================================ 场景 2：跨章可见 + 时间锚点纪律

async def test_next_chapter_sees_prev_memory_and_state(tmp_path):
    """验收②：写 2 章——第 2 章 draft 提示词「看见」第 1 章：POV 经历（[第1章]
    锚点）/POV 卡【当前状态】行/【世界现状】块/【未回收承诺】提醒（第1章埋）；
    反向断言：预置的未来第 3 章记忆与第 2 章自身（草稿时尚未提取）一律不得出现。"""
    store = build_store(tmp_path, [
        chapter(1, "题一", [beat("第1章第1拍"), beat("第1章第2拍")]),
        chapter(2, "题二", [beat("第2章第1拍"), beat("第2章第2拍")]),
    ])
    mem = MemoryStore(store.root)
    # 时间锚点纪律反向断言的「未来材料」：第 3 章记忆预置（含 POV 经历/信息项/承诺），
    # 晚于第 2 章——第 2 章的提示词绝不得看见（Review Focus #2）
    mem.write_chapter_memory(3, "题三", {
        "events": [{"title": "未来事件", "summary": "未来三章标记事件", "location": "",
                    "time_marker": "", "characters": ["甲"], "new_info": [],
                    "foreshadow": ""}],
        "character_events": [{"character": "甲", "summary": "未来三章标记经历",
                              "quotes": "", "acted": "", "felt": "",
                              "got_lost": "", "secrets": ""}],
        "info_updates": [{"title": "未来图谱", "content": "c", "revealed_to": ["甲"]}],
        "promise_updates": [{"kind": "promise", "content": "未来承诺标记",
                             "by_character": "甲", "status": "pending",
                             "resolution": "", "due": 5}],
    })

    def extract_for(prompt):
        if "稿1号" in prompt or "稿2号" in prompt:            # 【章节正文】锚点分章
            return extract_payload_ch1()
        if "稿3号" in prompt or "稿4号" in prompt:
            return extract_payload_ch2()
        raise AssertionError("提取提示词未带章正文锚点")

    events = EventLog(store.root)
    llm, calls = make_llm(writing_router(extract_for, select=SELECT_CAST))
    result = await run_write(store, llm, events)
    assert "成功 2 章" in result.text

    stream_calls = [b for b in calls if b.get("stream")]
    assert len(stream_calls) == 4                               # 两章各两拍
    for body in stream_calls[2:]:                               # 第 2 章的两拍提示词
        prompt = body["messages"][-1]["content"]
        assert "第2章第1拍" in prompt or "第2章第2拍" in prompt   # 锁定第 2 章的上下文
        # —— 正向：看见第 1 章的记忆与现状 ——
        assert "【视点角色经历档案】" in prompt
        assert "· [第1章] 甲夺得铜镜" in prompt                  # POV 经历 + 时间锚点
        assert "【当前状态（实际，以此为准）】" in prompt          # POV 卡现状行（第 1 章投影）
        assert "修为/等级：筑基后期 Lv12" in prompt
        assert "所在：山寨" in prompt
        assert "【世界现状（**会变的状态**，以此为准）】" in prompt
        assert "- [territory] 青石城：二级镇 · 粮320石" in prompt
        assert "【未回收承诺/伏笔提醒】" in prompt
        assert "第1章埋" in prompt and "铜镜来历必查" in prompt
        assert "【知识状态（严格遵守）】" in prompt
        assert "铜镜图谱：甲 知道（第1章起）" in prompt
        # —— 反向：时间锚点纪律——未来材料（第 3 章）与第 2 章自身（草稿时未提取）
        #     一律不得出现 ——
        assert "未来三章标记" not in prompt
        assert "未来承诺标记" not in prompt and "未来图谱" not in prompt
        assert "[第3章]" not in prompt
        assert "甲毁掉铜镜" not in prompt                       # 第 2 章自身记忆（提取在草稿之后）
        assert "· [第2章]" not in prompt and "· [第3章]" not in prompt   # 记忆行锚点（模板示例文案「[第2章]、[时间未知]」不计）

    # 链尾：两章都提取成功、digest 投影两章齐全
    lifecycle = [e for e in read_events(store) if e["kind"] != "draft_progress"]
    assert [(e["kind"], e["payload"]["seq"]) for e in lifecycle] \
        == [("chapter_start", 1), ("chapter_done", 1),
            ("chapter_start", 2), ("chapter_done", 2)]
    assert all(e["payload"]["memory_status"] == "extracted" for e in lifecycle
               if e["kind"] == "chapter_done")
    digest = (store.root / "memory" / "digests" / "vol-01.md").read_text(encoding="utf-8")
    assert "### 第1章《题一》" in digest and "### 第2章《题二》" in digest
    state = mem.read_state()
    assert state["characters"]["甲"]["location"] == "废墟"      # 第 2 章现状投影覆盖


# ================================================================ 场景 3：回定位（物件/台词 → 章 + 逐字引文）

_LAMP_LINE = "第{k}折：甲在祭坛深处点亮一盏青铜灯，灯焰泛着青光。甲低声说：灯不灭，人不散。"


def lamp_chunks(n_chars=1400):
    """带「青铜灯」物件与「灯不灭，人不散」台词的第 1 章正文（逐折变编号防严格循环）。"""
    chunks, total, k = [], 0, 1
    while total < n_chars:
        c = _LAMP_LINE.format(k=k) + "\n\n"
        chunks.append(c)
        total += len(c)
        k += 1
    return chunks


def _verified_hits(store, query: str) -> list[tuple[int, str]]:
    """search_chapters 工具层口径：解析观测行 → (章号, 引文)，逐字引文经
    locate_quote 在该章正文回验（验收③的「定位到章与逐字引文」）。"""
    out = search_chapters(store, {"query": query})
    assert out != "search_chapters：无命中", f"「{query}」应命中已写的物件/台词"
    hits = []
    for ln in out.splitlines():
        m = _HIT_LINE.match(ln.strip())
        assert m, f"观测行格式不符：{ln!r}"
        seq, quote = int(m.group(1)), m.group(3)
        assert locate_quote(store.read_chapter(seq).body, quote) is not None, \
            f"第{seq}章引文未通过 locate_quote 逐字回验：{quote!r}"
        hits.append((seq, quote))
    return hits


async def test_backreference_locates_chapter_and_quote(tmp_path):
    """验收③：第 1 章正文写过青铜灯与台词——经 search_chapters 工具层
    （物件/台词两个查询）定位到第 1 章 + 逐字引文；再经 write_plot 全链写第 2 章
    （细纲点名青铜灯），其 draft 提示词的【伏笔回指】块同口径定位（章号 + 逐字回验）。"""
    store = build_store(tmp_path, [
        chapter(1, "题一", [beat("第1章第1拍"), beat("第1章第2拍")]),
        chapter(2, "题二", [beat("第2章第1拍：甲循着青铜灯的微光追出祭坛"),
                            beat("第2章第2拍")]),
    ])

    state = {"n": 0}

    def router(body):
        prompt = body["messages"][-1]["content"]
        if body.get("stream"):
            assert "【任务】" in prompt and "只写" in prompt
            state["n"] += 1
            if state["n"] <= 2:                                 # 第 1 章两拍：带物件/台词的正文
                return {"chunks": lamp_chunks(), "usage": U(5, 10)}
            return {"chunks": draft_chunks(f"回{state['n']}号"), "usage": U(5, 10)}
        if "【资源目录（可挑选的内容）】" in prompt:
            return SELECT_OK, U(1, 2)
        if "【待审正文】" in prompt:
            return REVIEW_PASS, U(3, 4)
        if "你是小说记忆整理员" in prompt:                      # 记忆提取照常（不经回指断言）
            return extract_payload_ch1(), U(2, 3)
        raise AssertionError(f"路由不到任何模板锚点：{prompt[:80]}…")

    events = EventLog(store.root)
    llm, calls = make_llm(router)
    result = await run_write(store, llm, events)
    assert "成功 2 章" in result.text

    ch1 = store.read_chapter(1)
    assert "青铜灯" in ch1.body and "灯不灭，人不散" in ch1.body
    ch2 = store.read_chapter(2)
    assert "青铜灯" not in ch2.body                            # 命中唯一指向第 1 章

    # 口径一：search_chapters 工具层（物件 + 台词两个查询）
    for query, needle in (("青铜灯", "青铜灯"), ("灯不灭，人不散", "灯不灭，人不散")):
        hits = _verified_hits(store, query)
        assert hits and all(seq == 1 for seq, _ in hits), f"「{query}」应只定位第 1 章：{hits}"
        assert all(needle in quote for _, quote in hits)

    # 口径二：write_plot 全链的【伏笔回指】块（第 2 章细纲点名青铜灯）
    stream_calls = [b for b in calls if b.get("stream")]
    prompt = stream_calls[2]["messages"][-1]["content"]         # 第 2 章第 1 拍
    assert "第2章第1拍" in prompt
    assert "【伏笔回指】" in prompt
    m = re.search(r"^· 第1章《题一》：「(.+)」$", prompt, re.MULTILINE)
    assert m, f"回指块缺少第 1 章观测行：{prompt[-400:]}"
    assert "青铜灯" in m.group(1)
    assert locate_quote(ch1.body, m.group(1)) is not None      # 逐字引文回验（Review Focus #5）
    assert "第2章《" not in prompt.split("【伏笔回指】")[-1] \
        or "第2章《" not in m.group(0)                          # 不回指未来/本章（语料唯第 1 章）


# ================================================================ 零 LLM 判别：bundle 可复现

def test_memory_bundle_reproducible(tmp_path):
    """test_flavor_reproducible 同款零 LLM 判别：同一记忆库 build_memory_bundle
    两次（同一 index/查询/cur_seq），五块注入文本逐字一致、hits 一致。"""
    info = create_project(tmp_path, "复现书")
    mem = MemoryStore(info.path)
    mem.write_chapter_memory(1, "题一", {
        "events": [{"title": "夺镜", "summary": "甲在山寨夺得铜镜，血脉图谱随之现世",
                    "location": "山寨", "time_marker": "夜", "characters": ["甲", "乙"],
                    "new_info": [], "foreshadow": ""}],
        "character_events": [
            {"character": "甲", "summary": "甲夺得铜镜", "quotes": "灯在我手",
             "acted": "", "felt": "", "got_lost": "得铜镜", "secrets": ""},
            {"character": "乙", "summary": "寨外放哨", "quotes": "", "acted": "",
             "felt": "", "got_lost": "", "secrets": ""},
        ],
        "info_updates": [{"title": "铜镜图谱", "content": "铜镜纹路图谱",
                          "revealed_to": ["甲"]}],
        "promise_updates": [{"kind": "promise", "content": "铜镜来历必查",
                             "by_character": "甲", "status": "pending",
                             "resolution": "", "due": 3}],
    })
    mem.write_chapter_memory(2, "题二", {
        "events": [], "character_events": [
            {"character": "甲", "summary": "甲毁掉铜镜", "quotes": "了断",
             "acted": "", "felt": "", "got_lost": "失铜镜", "secrets": ""}],
        "info_updates": [], "promise_updates": []})
    index = Index(build_corpus(info.path))
    kwargs = {"mem": mem, "index": index, "pov": "甲", "cast_names": ["乙"],
              "cur_seq": 2, "query": "血脉图谱"}
    b1 = build_memory_bundle(**kwargs)
    b2 = build_memory_bundle(**kwargs)
    for field in ("pov_memories", "other_char_events", "knowledge_state",
                  "related_events", "open_promises"):
        assert getattr(b1, field) == getattr(b2, field) and getattr(b1, field), field
    assert b1.hits == b2.hits and b1.any_hits()
