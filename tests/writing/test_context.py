# 来源：契约测试（d:/project/local_dream/.superpowers/sdd/2026-11-13-p3-writing-pipeline/
# task-5-brief.md Step 1）；渲染判据逐字来自 web_dream backend/app/llm/assembler.py
#（catalog 337-350 / _retrieve_cast 483-548 降级 / _beats_text 642-695 / build_draft_prompt
#  189-334 的 P3 存活子集），键集与 tests/test_p3_prompts.py 的 VAR_CONTRACT 双向相等。
"""harness.writing.context 的契约测试：写作上下文组装 + 预算裁剪 + build_*_vars 键集。

夹具全部走真项目（tmp_path + create_project + ProjectStore 写盘）——本模块是
**文件格式 → LLM 提示词**的接缝，测试必须钉死「只经 store 读」的取值口径。
"""
import re

import pytest

from harness.memory import MemoryStore
from harness.projects import create_project
from harness.retrieval.corpus import Index, build_corpus
from harness.store.frontmatter import render_document
from harness.store.project import ChapterDoc, ProjectStore
from harness.writing import context
from harness.writing.flavor import detect

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# tests/test_p3_prompts.py VAR_CONTRACT 的对应子集（Task 5 组装层消费的 6 个模板；
# 键集必须双向相等，防拼写漂移）
CONTEXT_SELECT_VARS = {"objective", "beats", "target_beat", "catalog"}
REVIEW_VARS = {"chapter_seq", "world_entries", "pov_memories", "knowledge_state",
               "cast_cards", "outline", "draft"}
REVISE_VARS = {"issues", "flavor_hits", "outline", "world_entries", "cast_cards",
               "pov_memories", "knowledge_state", "draft"}
REWRITE_BEAT_VARS = {"outline", "prev_text", "beat_text", "next_text", "issues"}
DEAIFY_VARS = {"style_guide", "style_sample", "flavor_hits", "draft"}
DEAIFY_EDITS_VARS = {"style_guide", "style_sample", "targets"}

BEATS = [
    {"pov": "甲", "location": "山门", "event": "甲上山拜师"},
    {"pov": "甲", "location": "大殿", "event": "长老出题刁难"},
    {"pov": "甲", "location": "演武场", "event": "甲一鸣惊人"},
]

CHAPTER_META = {
    "seq": 3, "title": "拜师", "status": "writing",
    "objective": "拜入宗门立住脚",
    "scene": "青石宗山门至演武场，清晨",
    "characters": [{"name": "甲", "role": "主角"}, {"name": "长老", "role": "考官"}],
    "dialogue_hooks": ["这账不对。"],
    "humor_points": ["测灵石炸屏"],
    "ending_hook": "长老身份存疑",
}


@pytest.fixture()
def store(tmp_path):
    info = create_project(tmp_path, "写作链书",
                          story={"genre": "玄幻", "tone": "热血升级", "ending": "逆天改命圆满"})
    return ProjectStore(info.path)


def _chapter_doc() -> ChapterDoc:
    return ChapterDoc(seq=3, meta=dict(CHAPTER_META), body="")


def _plot() -> dict:
    return {"seq": 1, "title": "入门", "summary": "山村少年入宗门",
            "conflict": "资质被夺", "resolution": "逆天改命"}


# ================================================================ beats_text（seed 642-695 逐字）

def test_beats_text_lock_and_future_guard():
    out = context.beats_text(CHAPTER_META, BEATS, target_beat="长老出题刁难")
    assert "⚠️ 本次只写第 2 拍：甲｜大殿｜长老出题刁难" in out
    assert "——场景锁定「大殿」，只出现本拍涉及的人物与状态" in out
    assert "🚫 后续节拍尚未发生（严禁提前写其中的人物登场/苏醒/揭示/事件）：甲一鸣惊人" in out
    # 完整细纲结构（seed _beats_text 逐字形态）
    assert "场景：青石宗山门至演武场，清晨" in out
    assert "出场人物：甲（主角）；长老（考官）" in out
    assert "情节（按顺序扩写）：" in out
    assert "1. 甲｜山门｜甲上山拜师" in out
    assert "对话要点：这账不对。" in out
    assert "爽点设计：测灵石炸屏" in out
    assert "结尾钩子：长老身份存疑" in out
    # 命中不了节拍 → 待续写提示（seed else 分支）
    miss = context.beats_text(CHAPTER_META, BEATS, target_beat="不存在的拍")
    assert "→ 待续写节拍：不存在的拍" in miss
    assert "⚠️" not in miss
    # 无 beats 时只有目标节拍占位
    assert context.beats_text(CHAPTER_META, [], target_beat="X") == "（目标节拍：X）"


def test_beats_text_future_guard_fullwidth_separator():
    """「🚫 后续节拍尚未发生」清单用全角分号「；」分隔（seed verbatim 口径，
    曾误作半角 ";"——M-8）。"""
    beats = BEATS + [{"pov": "甲", "location": "后山", "event": "夜探藏经阁"}]
    out = context.beats_text(CHAPTER_META, beats, target_beat="长老出题刁难")
    assert "：甲一鸣惊人；夜探藏经阁" in out
    assert "甲一鸣惊人;夜探藏经阁" not in out
    assert context.beats_text(CHAPTER_META, [], target_beat="") == ""


# ================================================================ world_entries_block

def test_world_entries_block_title_match(store):
    store.upsert_world_entries([
        {"title": "青石宗", "category": "势力",
         "summary": "边陲小宗", "content": "青石宗传功长老 mysterious", "importance": "高"},
        {"title": "测灵石", "category": "器物", "summary": "s", "content": "c" * 300,
         "importance": "中"},
    ])
    out = context.world_entries_block(store, ["青石宗", "测灵石"])
    assert "【青石宗】青石宗传功长老 mysterious" in out
    # content[:150] 截断
    assert "【测灵石】" + "c" * 150 in out
    assert "c" * 151 not in out
    # 未命中标题 → 不出现
    assert context.world_entries_block(store, ["不存在的条目"]) == ""
    # 空入参 → 空串
    assert context.world_entries_block(store, []) == ""


# ================================================================ cast_cards_block

def test_cast_cards_tolerant(store):
    # P0 平铺 KV 卡：profile 键平铺在 front-matter（upsert_character 原样往返）
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "aliases": ["小甲"],
                            "speech_style": "话极少，能点头就不开口"})
    # E.4 三节卡（手造）：profile 键在正文 `键：值` 行（含中文键名）
    body = ("# 乙\n\n## 基础\n\n外貌：平平无奇，扔人堆里找不着\n\n"
            "## 性格与口吻\n\n性格：外冷内热\n说话风格：惜字如金，能用三个字不用四个字")
    card = render_document({"name": "乙", "role_type": "supporting",
                            "aliases": ["阿乙"]}, body)
    (store.root / "characters" / "乙.md").write_text(card, encoding="utf-8")
    # 缺键卡：什么都没有也不炸
    store.upsert_character({"name": "丙", "role_type": "supporting"})

    out = context.cast_cards_block(store, ["甲", "乙", "丙"])
    assert "- 甲（protagonist）｜别名/真名：小甲" in out
    assert "说话风格：话极少，能点头就不开口" in out
    assert "- 乙（supporting）｜别名/真名：阿乙" in out
    assert "性格：外冷内热" in out
    assert "说话风格：惜字如金，能用三个字不用四个字" in out
    assert "- 丙（supporting）" in out
    # 无卡名 → 未卡名列表（沿用细纲名的提醒，seed 逐字）
    out2 = context.cast_cards_block(store, ["甲", "神秘人"])
    assert "- 本章还出场（**没有角色卡**）：神秘人" in out2
    assert "不要另起名/改名" in out2
    # 空入参 → 空串
    assert context.cast_cards_block(store, []) == ""


# ================================================================ catalog（seed 337-350）

def test_catalog_sections(store):
    store.upsert_world_entries([{"title": "青石宗", "category": "势力",
                                 "summary": "边陲小宗", "content": "内容" * 30,
                                 "importance": "高"}])
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "profile": {"background": "寒门出身" * 10}})
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "开篇",
                                                "status": "done"}, body="x"))
    out = context.catalog(store)
    assert "【设定条目】" in out and "- 青石宗：" in out
    # content[:40] 截断（30 组 ×2 字 = 60 字，只留前 40）
    assert "内容" * 25 not in out
    assert "【角色】" in out and "- 甲（protagonist）：寒门出身" in out
    # 本地无摘要链 → 摘要节只有节头（无行）
    chapter_part = out.split("【章节摘要】", 1)[1]
    assert chapter_part.strip() == ""


# ================================================================ build_draft_context

def _selected() -> dict:
    return {"world_titles": ["青石宗"], "character_names": ["甲"],
            "keywords": ["青石宗"], "need_web": []}


def test_story_block_from_readme(store):
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "speech_style": "寡言"})
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                     prev_tail="", selected=_selected())
    assert "题材：玄幻" in dc.system
    assert "基调：热血升级" in dc.system
    assert "结局走向：逆天改命圆满" in dc.system
    assert "通用写作规则" in dc.system  # global.base_prompt 在内


def test_build_draft_context_core_blocks(store):
    store.upsert_world_entries([{"title": "青石宗", "category": "势力",
                                 "summary": "边陲小宗", "content": "传功一脉单传",
                                 "importance": "高"}])
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "speech_style": "寡言"})
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                     prev_tail="上一章的最后一段文字",
                                     selected=_selected(),
                                     target_beat="长老出题刁难",
                                     recent_draft="已经写出的正文尾部" * 50)
    assert "【当前卷大纲】\n山村少年入宗门" in dc.user
    assert "【本章目标】\n拜入宗门立住脚" in dc.user
    assert "【本章细纲】" in dc.user and "⚠️ 本次只写第 2 拍" in dc.user
    assert "【出场角色卡" in dc.user and "说话风格：寡言" in dc.user
    assert "【世界观设定（检索命中）】\n【青石宗】传功一脉单传" in dc.user
    assert "【最近正文】" in dc.user and "上一章的最后一段文字" in dc.user
    assert "【任务】" in dc.user and "只写「长老出题刁难」对应的**这一拍**" in dc.user
    # 模板空槽已清理：不留 {{var}} 与空区块头
    assert not _VAR_RE.search(dc.user)
    assert "【视点角色经历档案】" not in dc.user
    assert "【前情提要】" not in dc.user
    # materials 五键（review/修正/deaify 复用）
    assert set(dc.materials) == {"world_entries", "cast_cards", "pov_memories",
                                 "knowledge_state", "outline"}
    assert "甲上山拜师" in dc.materials["outline"]


def test_budget_trim_drops_droppable_keeps_core(store, monkeypatch):
    store.upsert_world_entries([{"title": "青石宗", "category": "势力",
                                 "summary": "边陲小宗", "content": "传功一脉单传",
                                 "importance": "高"}])
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "speech_style": "寡言"})
    # world_entries_block 每键 [:150] × top5，物理上灌不爆 40000 真预算；
    # 收窄预算走同一裁剪路径（droppable 按序丢、recent_draft 降级尾 3000、core/task 不丢）
    monkeypatch.setattr(context, "BUDGET_CHARS", 200)
    recent = "R" * 1500 + "最近正文的结尾标记"
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                     prev_tail="", selected=_selected(),
                                     target_beat="长老出题刁难", recent_draft=recent)
    # world 块被裁掉
    assert "【世界观设定（检索命中）】" not in dc.user
    assert "传功一脉单传" not in dc.user
    # recent_draft 不丢：降级尾 3000（此处块本身 < 3000，整块保留）
    assert "最近正文的结尾标记" in dc.user
    # core + task 永不丢
    assert "【本章目标】" in dc.user
    assert "【本章细纲】" in dc.user and "⚠️ 本次只写第 2 拍" in dc.user
    assert "【出场角色卡" in dc.user
    assert "【任务】" in dc.user


def _degraded_block(user: str, marker: str) -> str:
    """从 user 里取出含 marker 的那一段（降级保留后的【最近正文】块）。"""
    for chunk in user.split("\n\n"):
        if marker in chunk:
            return chunk
    return ""


def test_budget_cut_at_sentence_boundary(store, monkeypatch):
    """M-12：预算降级保留的切分点落在**句末标点**——被拦腰截断的半句整体丢弃。

    构造：prev_tail 2000 字（其中第 550 字处有一个「。」）+ recent_draft 1500 字
    → 【最近正文】块共 3508 字 > 3000 → 走降级路径。
    旧版硬切会把「A…A。」拦腰截断（窗口里留下约 49 个 "A"）；新版从句末标点**之后**开始
    保留 → 保留块里**一个 "A" 都没有**（本断言对旧硬切实现为红，钉得住行为）。
    """
    monkeypatch.setattr(context, "BUDGET_CHARS", 200)
    prev = "A" * 550 + "。" + "B" * 1449
    recent = "短句。" * 500
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                     prev_tail=prev, selected=_selected(),
                                     target_beat="长老出题刁难", recent_draft=recent)

    block = _degraded_block(dc.user, "B" * 1449)

    assert block, "降级保留块没找到"
    assert "A" not in block          # 旧硬切会留下约 49 个 A
    assert "B" * 1449 in block
    assert "短句。" in block


def test_budget_cut_falls_back_to_hard_cut_without_sentence_end(store, monkeypatch):
    """M-12：窗口内无句末标点 → **确实硬切**（未因句末标点额外截断）。

    主断言：保留块 == 硬切结果（长度 == 3000）。
    对照断言（防空测）：窗口**有**句末标点时必须切在标点之后——旧硬切实现会保留半句，
    对照断言为红；只钉「无标点时硬切」对两种实现同为绿（空测）。
    """
    monkeypatch.setattr(context, "BUDGET_CHARS", 200)
    prev = "A" * 2000                      # 全文无句末标点
    recent = "短句。" * 500
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                     prev_tail=prev, selected=_selected(),
                                     target_beat="长老出题刁难", recent_draft=recent)

    block_text = "【最近正文】\n" + prev + "\n" + recent
    hard_cut = block_text[-3000:]
    assert len(hard_cut) == 3000
    assert hard_cut in dc.user            # 确实硬切（未因句末标点额外截断）

    # 对照：窗口内有句末标点 → 切在标点之后（旧硬切保留半句 → 本断言红）
    prev2 = "A" * 550 + "。" + "B" * 1449
    dc2 = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS,
                                      prev_tail=prev2, selected=_selected(),
                                      target_beat="长老出题刁难", recent_draft=recent)
    assert "A" not in _degraded_block(dc2.user, "B" * 1449)


# ================================================================ context_select_vars / build_*_vars 键集

def test_context_select_vars_key_set():
    out = context.context_select_vars(CHAPTER_META, BEATS, "目录串", target_beat="X")
    assert set(out) == CONTEXT_SELECT_VARS
    assert out["objective"] == "拜入宗门立住脚"
    assert out["catalog"] == "目录串"
    assert "1. 甲｜山门｜甲上山拜师" in out["beats"]


def _materials() -> dict:
    return {"world_entries": "【青石宗】传功一脉单传", "cast_cards": "- 甲（protagonist）",
            "pov_memories": "", "knowledge_state": "",
            "outline": "情节（按顺序扩写）：\n1. 甲｜山门｜甲上山拜师"}


def test_build_review_vars_key_set():
    out = context.build_review_vars(_materials(), 3, "待审正文")
    assert set(out) == REVIEW_VARS
    assert out["chapter_seq"] == "3" and out["draft"] == "待审正文"
    assert out["outline"].startswith("情节（按顺序扩写）")


def test_build_revise_vars_key_set():
    issues = [{"type": "logic", "fix_kind": "rewrite", "issue": "因果颠倒",
               "quote": "", "replacement": "", "suggestion": "先因后果"}]
    report = detect("不是感激。是算账。他知道，属于他的时代才刚刚开始。")
    out = context.build_revise_vars(_materials(), issues, report, "待修正正文")
    assert set(out) == REVISE_VARS
    assert "因果颠倒" in out["issues"] and "先因后果" in out["issues"]
    assert "否定对偶" in out["flavor_hits"] or "总结升华" in out["flavor_hits"]
    assert out["draft"] == "待修正正文"


def test_build_rewrite_beat_vars_key_set():
    issues = [{"type": "outline", "fix_kind": "rewrite", "issue": "场景不对"}]
    out = context.build_rewrite_beat_vars(_materials(), "本拍正文", "上拍结尾",
                                          "下拍开头", issues)
    assert set(out) == REWRITE_BEAT_VARS
    assert out["beat_text"] == "本拍正文" and out["prev_text"] == "上拍结尾"
    assert out["next_text"] == "下拍开头" and "场景不对" in out["issues"]
    assert out["outline"].startswith("情节（按顺序扩写）")


def test_build_deaify_vars_key_set():
    report = detect("不是感激。是算账。他知道，属于他的时代才刚刚开始。")
    out = context.build_deaify_vars(report, "待去味正文")
    assert set(out) == DEAIFY_VARS
    assert out["draft"] == "待去味正文"
    assert out["flavor_hits"].strip()
    assert out["style_guide"] == "" and out["style_sample"] == ""  # TODO(P5) skill


def test_build_deaify_edits_vars_key_set():
    out = context.build_deaify_edits_vars("1. 原文「不是感激。」｜判定：…")
    assert set(out) == DEAIFY_EDITS_VARS
    assert out["targets"].startswith("1. 原文")
    assert out["style_guide"] == "" and out["style_sample"] == ""


# ================================================================ P4 接线：mem/index 注入（Task 7）

def _layers(cevents=None, events=None, infos=None, promises=None) -> dict:
    return {"events": events or [], "character_events": cevents or [],
            "info_updates": infos or [], "promise_updates": promises or []}


def _p4_doc(seq: int = 3, characters=None) -> ChapterDoc:
    meta = dict(CHAPTER_META)
    meta["seq"] = seq
    meta["plot_id"] = "1-1"
    meta["characters"] = characters if characters is not None else \
        [{"name": "甲", "role": "主角"}, {"name": "长老", "role": "考官"}]
    return ChapterDoc(seq=seq, meta=meta, body="")


def _write_chapter_memory(mem: MemoryStore, seq: int, layers: dict) -> None:
    mem.write_chapter_memory(seq, f"第{seq}章", layers)


def test_context_mem_none_backwards_compatible(store):
    """旧签名（无 mem/index）：五块返 ""、materials 五键、记忆区块不渲染。"""
    store.upsert_character({"name": "甲", "role_type": "protagonist"})
    dc = context.build_draft_context(store, _p4_doc(), _plot(), BEATS,
                                     prev_tail="", selected=_selected())
    assert set(dc.materials) == {"world_entries", "cast_cards", "pov_memories",
                                 "knowledge_state", "outline"}
    assert dc.materials["pov_memories"] == "" and dc.materials["knowledge_state"] == ""
    for head in ("【视点角色经历档案】", "【知识状态", "【相关历史事件】",
                 "【未回收承诺/伏笔提醒】", "【前情提要】", "【其他角色相关经历】",
                 "【世界现状"):
        assert head not in dc.user


def test_context_mem_fills_materials_and_blocks(store):
    """mem+index 给时：P4 五键激活、五块进 user、POV 卡附现状行、世界现状独立块。"""
    store.upsert_character({"name": "甲", "role_type": "protagonist",
                            "speech_style": "寡言"})
    store.upsert_world_entries([{"title": "青石宗", "category": "势力",
                                 "summary": "s", "content": "传功一脉单传",
                                 "importance": "高"}])
    mem = MemoryStore(store.root)
    _write_chapter_memory(mem, 1, _layers(
        cevents=[{"character": "甲", "summary": "甲上山拜师", "quotes": "",
                  "acted": "", "felt": "", "got_lost": "", "secrets": ""}],
        events=[{"title": "青石宗山门", "summary": "青石宗山门变故", "location": "山门",
                 "time_marker": "", "characters": ["甲"]}],
        infos=[{"title": "血脉图谱", "content": "c", "revealed_to": ["甲"]}],
        promises=[{"kind": "promise", "content": "长老之约", "by_character": "长老",
                   "status": "open", "due": 4, "resolution": ""}]))
    mem.apply_state_updates({
        "characters": [{"name": "甲", "realm": "凡界", "level": "3",
                        "location": "山门"}],
        "world": [{"kind": "sect", "key": "青石宗", "label": "声望", "value": "100",
                   "importance": 5}],
    }, seq=1)
    index = Index(build_corpus(store.root))
    dc = context.build_draft_context(store, _p4_doc(), _plot(), BEATS,
                                     prev_tail="", selected=_selected(),
                                     mem=mem, index=index)
    # materials：旧五键 + P4 三新键（related_events/open_promises/rolling_summary）
    assert set(dc.materials) == {"world_entries", "cast_cards", "pov_memories",
                                 "knowledge_state", "outline", "related_events",
                                 "open_promises", "rolling_summary"}
    # 五块进 user（带时间锚点）
    assert "【视点角色经历档案】\n· [第1章] 甲上山拜师" in dc.user
    assert "【知识状态（严格遵守）】\n· 血脉图谱：甲 知道（第1章起）" in dc.user
    assert "【相关历史事件】\n· [第1章] 青石宗山门变故" in dc.user
    assert "【未回收承诺/伏笔提醒】\n· [承诺｜第1章埋，临近到期（计划第4章回收）] 长老之约" in dc.user
    # POV 卡附运行时现状行（seed _state_line）
    assert "【当前状态（实际，以此为准）】修为/等级：凡界 Lv3；所在：山门" in dc.user
    # 世界现状块（seed _retrieve_states 形状）
    assert "【世界现状（**会变的状态**，以此为准）】\n- [sect] 声望：100" in dc.user
    # knowledge_state 材料尾部带世界现状（review/修正复用）
    assert dc.materials["knowledge_state"].endswith("- [sect] 声望：100")
    assert dc.materials["pov_memories"].startswith("· [第1章]")
    assert "甲上山拜师" in dc.materials["outline"]


def test_cast_offstage_filter_unless_named_in_beats(store):
    """dead/exited 角色：细纲点名 → 出卡（带已死亡行）；未点名 → 不出卡。"""
    for name in ("甲", "乙", "丙", "丁"):
        store.upsert_character({"name": name, "role_type": "supporting"})
    mem = MemoryStore(store.root)
    mem.apply_state_updates({"characters": [
        {"name": "乙", "status": "dead", "last_seen": 2},
        {"name": "丙", "status": "exited", "last_seen": 9},
    ]}, seq=3)
    # 细纲 characters 点名 甲/乙（乙已死但点名）；丙退场未点名；丁活着
    doc = _p4_doc(characters=[{"name": "甲", "role": "主角"},
                              {"name": "乙", "role": "旧识"}])
    selected = {"world_titles": [], "character_names": ["甲", "乙", "丙", "丁"]}
    dc = context.build_draft_context(store, doc, _plot(), BEATS, prev_tail="",
                                     selected=selected, mem=mem)
    cast = dc.materials["cast_cards"]
    assert "- 乙（supporting）" in cast            # 点名死者仍注入（回顾/插叙章口径）
    assert "状态：已死亡" in cast
    assert "- 丙（supporting）" not in cast        # 退场未点名 → 不出卡
    assert "本章还出场" not in cast or "丙" not in cast.split("本章还出场")[-1]
    assert "- 丁（supporting）" in cast


def test_cast_last_seen_ordering(store):
    """其余角色按 last_seen_chapter 降权排序（seed _cast_order :589-623 补位降权）。"""
    for name in ("甲", "乙", "丙", "丁"):
        store.upsert_character({"name": name, "role_type": "supporting"})
    mem = MemoryStore(store.root)
    mem.apply_state_updates({"characters": [
        {"name": "乙", "last_seen": 1},
        {"name": "丙", "last_seen": 9},
    ]}, seq=3)
    doc = _p4_doc(characters=[{"name": "甲", "role": "主角"}])
    selected = {"world_titles": [], "character_names": ["丁", "甲", "丙", "乙"]}
    dc = context.build_draft_context(store, doc, _plot(), BEATS, prev_tail="",
                                     selected=selected, mem=mem)
    cast = dc.materials["cast_cards"]
    # 视点 甲 最前；其余按 last_seen 降序：丙(9) → 乙(1) → 丁(无 state=0)
    order = [cast.index(f"- {n}（supporting）") for n in ("甲", "丙", "乙", "丁")]
    assert order == sorted(order)
    assert order[0] == cast.index("- 甲（supporting）")


def test_digest_injected_as_rolling_summary(store):
    """rolling_summary = 本卷 digest 全文（DigestKeeper.read），未来章过滤（Review Focus #2）。"""
    store.upsert_character({"name": "甲", "role_type": "protagonist"})
    mem = MemoryStore(store.root)
    digest_path = store.root / "memory" / "digests" / "vol-01.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(
        "# 第1卷前情提要\n\n## 情节 1. 入门\n\n### 第1章《开篇》\n"
        "- 起程——离开山村\n\n### 第9章《未来章》\n- 未来事件爆发\n", encoding="utf-8")
    dc = context.build_draft_context(store, _p4_doc(seq=3), _plot(), BEATS,
                                     prev_tail="", selected=_selected(), mem=mem)
    assert "【前情提要】" in dc.user
    assert "起程——离开山村" in dc.user
    assert "未来事件爆发" not in dc.user          # 第 9 章 > cur_seq=3 → 剔除
    assert "### 第9章" not in dc.materials["rolling_summary"]
    assert dc.materials["rolling_summary"].startswith("# 第1卷前情提要")


def test_context_memory_future_excluded_in_all_blocks(store):
    """Review Focus #2：cur_seq=3 时第 5 章的记忆不进任何块（含 POV/承诺/知识）。"""
    store.upsert_character({"name": "甲", "role_type": "protagonist"})
    mem = MemoryStore(store.root)
    _write_chapter_memory(mem, 1, _layers(
        cevents=[{"character": "甲", "summary": "第一章经历", "quotes": "",
                  "acted": "", "felt": "", "got_lost": "", "secrets": ""}],
        promises=[{"kind": "promise", "content": "旧承诺", "by_character": "甲",
                   "status": "open", "due": None, "resolution": ""}]))
    _write_chapter_memory(mem, 5, _layers(
        cevents=[{"character": "甲", "summary": "第五章未来经历", "quotes": "",
                  "acted": "", "felt": "", "got_lost": "", "secrets": ""}],
        promises=[{"kind": "promise", "content": "未来承诺", "by_character": "甲",
                   "status": "open", "due": None, "resolution": ""}]))
    dc = context.build_draft_context(store, _p4_doc(seq=3), _plot(), BEATS,
                                     prev_tail="", selected=_selected(), mem=mem)
    blob = dc.user + "\n" + "\n".join(dc.materials.values())
    assert "第五章未来经历" not in blob and "未来承诺" not in blob
    assert "[第5章]" not in blob
    assert "第一章经历" in dc.materials["pov_memories"] and "旧承诺" in dc.materials["open_promises"]


# ------------------------------------------------------------------ 现实参照块（P5 T10）

def test_web_facts_block_appears_when_given(store):
    """web_facts 传入 → droppable 尾部出现【现实参照】块。"""
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS, "",
                                     _selected(), recent_draft="",
                                     web_facts="· 率土之滨 内政\n内政含屯田与征兵")
    assert "【现实参照】" in dc.user
    assert "率土之滨 内政" in dc.user


def test_web_facts_block_absent_by_default(store):
    """不传 web_facts → 无该块（现状不变）。"""
    dc = context.build_draft_context(store, _chapter_doc(), _plot(), BEATS, "",
                                     _selected(), recent_draft="")
    assert "【现实参照】" not in dc.user
