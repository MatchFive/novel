"""P37：世界坐标（seed `_world_variation` 本地移植，brief 见 sdd/2026-11-19-p37-world-variation）。

口径钉死（brief）：
- 持久化 `<root>/.harness/world_variation.json`（seed 的 Project.settings 换文件）；
  项目自定义黑名单 `<root>/.harness/banned_tropes.json`；坏档 → 空当无坐标。
- 短路链（seed 逐字语义）：经典款信号 → 落 {"canon": true} + _CANON_BLOCK（正典优先）；
  已存坐标 → 原样渲染；世界有条目 → 不生成（返回 (None, "")，零 LLM 零落盘）。
- 生成链：draft 6 候选（standard/1.0/8192）→ 黑名单/旧作重复代码层淘汰 →
  跨 provider judge（pro/0.2/4096；chosen 越界/失败 → None）→ _rule_pick（与旧作
  重合最少）→ _rule_variation（四轴池确定性兜底）→ 落盘。
- 注入：edit_by_command 世界目标 instruction 且已存坐标 → propose 提示词追加
  【本次创作坐标…】硬约束块（_render_variation 逐字）。
"""
from __future__ import annotations

import json
import random
from importlib import resources
from pathlib import Path

import pytest

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore
from harness.world_variation import (
    _CANON_BLOCK,
    _DEFAULT_BANNED_TROPES,
    _VARIATION_AXES,
    _core_repeated,
    _hits,
    _render_variation,
    _rule_pick,
    _rule_variation,
    _wants_canon,
    clear_variation,
    collect_used,
    draft_candidates,
    judge_pick,
    load_banned_tropes,
    pick_judge_config,
    read_variation,
    resolve_variation,
    variation_block,
    write_variation,
)

# --------------------------------------------------------------------- fixtures


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本：_Resp 为响应，Exception 抛；provider 三档钉死可断言路由。"""

    def __init__(self, script, *, standard="m-std", pro="m-pro"):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(base_url="https://api.test", api_key="k",
                                       lite="m-lite", standard=standard, pro=pro)

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        pass


@pytest.fixture
def store(tmp_path):
    info = create_project(tmp_path / "lib", "坐标书")
    return ProjectStore(info.path)


def make_settings(root: Path, *, two_providers: bool = False) -> Settings:
    providers = {"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                        lite="m", standard="m-std", pro="m-pro")}
    if two_providers:
        providers["judge"] = ProviderConfig(base_url="https://api.judge", api_key="k2",
                                            lite="m", standard="m-j", pro="m-jpro")
    return Settings(library_root=root, providers=providers,
                    default_provider="test", default_tier="standard", web_verify=True)

def _cands(*cores: str) -> list[dict]:
    return [{"core": c, "metaphor": f"隐喻{i}", "medium": "以技艺为媒",
             "godstate": "沉睡", "cost": "记忆可被交易", "why": f"理由{i}"}
            for i, c in enumerate(cores)]


_DRAFT_OK = json.dumps({"candidates": _cands("潮汐为凭的世界", "锈蚀吞城的大陆")},
                       ensure_ascii=False)
_JUDGE_OK = json.dumps({"reviews": [{"index": 0, "novelty": 5, "coherence": 5,
                                     "fit": 5, "writability": 5, "note": "n"}],
                        "chosen": 1, "reason": "第二套更新颖"}, ensure_ascii=False)


# --------------------------------------------------------------------- 纯函数


def test_hits_substring_and_empty():
    assert _hits("债务与账目", _DEFAULT_BANNED_TROPES) == ["债务", "账目"]
    assert _hits("干净文本", _DEFAULT_BANNED_TROPES) == []


def test_core_repeated_norm_and_length_gate():
    assert _core_repeated("潮汐为凭的世界", ["某某的潮汐为凭的世界呀"]) is True   # 互相包含
    assert _core_repeated("潮汐为凭的世界", ["完全不同内核命题"]) is False
    assert _core_repeated("短核", ["短核"]) is False                       # <6 字不算
    assert _core_repeated("潮 汐 为 凭 的 世", ["潮汐为凭的世界观啊"]) is True   # 去空白后比较


def test_wants_canon_any_text():
    assert _wants_canon("我要最经典款的剑与魔法", "", "")
    assert _wants_canon("", "走正统路线", "")
    assert not _wants_canon("原创设定", "新颖题材", "")


def test_rule_pick_least_overlap():
    used = {"metaphor": {"隐喻0"}, "medium": set(), "godstate": set(), "cost": set()}
    cands = _cands("甲", "乙")
    pick = _rule_pick(cands, used, random.Random(0))
    assert pick["core"] == "乙"                    # 隐喻0 重合的被淘汰后取重合最少


def test_rule_variation_deterministic_and_avoids_used():
    used = {key: set() for key, _, _ in _VARIATION_AXES}
    a = _rule_variation("proj-x", used)
    b = _rule_variation("proj-x", used)
    assert a == b                                   # 同 project_key 确定性
    assert set(a) == {key for key, _, _ in _VARIATION_AXES}
    used2 = {key: {a[key]} for key, _, _ in _VARIATION_AXES}
    c = _rule_variation("proj-y", used2)
    assert all(c[key] != a[key] for key in c)       # 逐轴避开已用（池够大）


def test_render_variation_hard_constraint_wording():
    block = _render_variation({"core": "世界是个伤口", "metaphor": "伤口",
                               "medium": "以记忆为媒", "why": "避开套路"})
    assert "【本次创作坐标（**硬约束**" in block
    assert "- 世界内核（一句话命题）：世界是个伤口" in block
    assert "默认套路" in block and "借债/还债/赎罪券" in block


# --------------------------------------------------------------------- 持久化


def test_variation_roundtrip_and_clear(store):
    write_variation(store.root, {"core": "x", "metaphor": "潮汐"})
    assert read_variation(store.root) == {"core": "x", "metaphor": "潮汐"}
    clear_variation(store.root)
    assert read_variation(store.root) == {}


def test_variation_bad_file_returns_empty(store):
    p = store.root / ".harness" / "world_variation.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_variation(store.root) == {}


def test_load_banned_tropes_default_plus_project_file(store):
    assert load_banned_tropes(store.root) == list(_DEFAULT_BANNED_TROPES)
    (store.root / ".harness" / "banned_tropes.json").write_text(
        json.dumps(["债务", "机甲", "机甲"], ensure_ascii=False), encoding="utf-8")
    out = load_banned_tropes(store.root)
    assert "机甲" in out and out.count("机甲") == 1     # 去重保序


def test_collect_used_scans_library_except_self(store, tmp_path):
    lib = tmp_path / "lib"
    other = create_project(lib, "旧书")
    write_variation(other.path, {"core": "旧内核命题", "metaphor": "潮汐",
                                 "medium": "以技艺为媒", "godstate": "沉睡",
                                 "cost": "记忆可被交易"})
    write_variation(store.root, {"core": "本项目的", "metaphor": "镜像"})
    used, cores = collect_used(lib, store.root)
    assert "潮汐" in used["metaphor"]
    assert "旧内核命题" in cores and "本项目的" not in cores   # except_root 生效


def test_pick_judge_config_other_provider():
    one = make_settings(Path("x"))
    assert pick_judge_config(one) is None               # 只有生成方 → 确定性兜底
    two = make_settings(Path("x"), two_providers=True)
    cfg = pick_judge_config(two)
    assert cfg is not None and cfg.pro == "m-jpro"      # ≠ default_provider 的那个


# --------------------------------------------------------------------- draft/judge


@pytest.mark.asyncio
async def test_draft_candidates_routing_and_vars(store):
    llm = FakeLLM([_Resp(_DRAFT_OK)])
    prompts = PromptLoader(home_dir=store.root)
    out = await draft_candidates(llm=llm, prompts=prompts, premise="少年修道",
                                 constraints="（无）", used="（无——这是该作者的第一本书）",
                                 banned="、".join(_DEFAULT_BANNED_TROPES))
    assert len(out) == 2 and out[0]["core"] == "潮汐为凭的世界"
    call = llm.calls[0]
    assert call["model"] == "m-std" and call["temperature"] == 1.0
    assert call["max_tokens"] == 8192
    for var in ("少年修道", "（无）", "（无——这是该作者的第一本书）", "债务"):
        assert var in call["prompt"]


@pytest.mark.asyncio
async def test_draft_candidates_drops_empty_core_and_failure(store):
    bad = json.dumps({"candidates": [{"core": "  ", "metaphor": "x"},
                                     {"core": "有效内核", "why": "w"}]}, ensure_ascii=False)
    llm = FakeLLM([_Resp(bad)])
    out = await draft_candidates(llm=llm, prompts=PromptLoader(home_dir=store.root),
                                 premise="p", constraints="c", used="u", banned="b")
    assert [c["core"] for c in out] == ["有效内核"]
    boom = FakeLLM([RuntimeError("api down")])
    assert await draft_candidates(llm=boom, prompts=PromptLoader(home_dir=store.root),
                                  premise="p", constraints="c", used="u", banned="b") is None


@pytest.mark.asyncio
async def test_judge_pick_chosen_and_fallbacks(store):
    llm = FakeLLM([_Resp(_JUDGE_OK)])
    cands = _cands("甲", "乙")
    pick = await judge_pick(llm=llm, prompts=PromptLoader(home_dir=store.root),
                            cands=cands, premise="p", constraints="c", used="u",
                            banned="b", judge_tag="judge")
    assert pick["core"] == "乙" and pick["judge_provider"] == "judge"
    assert pick["judge_reason"] == "第二套更新颖"
    assert "[1] core=乙" in llm.calls[0]["prompt"]
    call = llm.calls[0]
    assert call["model"] == "m-pro" and call["temperature"] == 0.2
    assert call["max_tokens"] == 4096
    # chosen 越界 → None；LLM 抛错 → None
    oob = FakeLLM([_Resp(json.dumps({"chosen": 9, "reviews": []}, ensure_ascii=False))])
    assert await judge_pick(llm=oob, prompts=PromptLoader(home_dir=store.root),
                            cands=cands, premise="p", constraints="c", used="u",
                            banned="b", judge_tag="judge") is None
    boom = FakeLLM([RuntimeError("x")])
    assert await judge_pick(llm=boom, prompts=PromptLoader(home_dir=store.root),
                            cands=cands, premise="p", constraints="c", used="u",
                            banned="b", judge_tag="judge") is None


# ------------------------------------------------------------- resolve_variation


@pytest.mark.asyncio
async def test_resolve_canon_short_circuit_zero_llm(store, tmp_path):
    llm = FakeLLM([])
    _picks, block = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"), llm=llm,
        prompts=PromptLoader(home_dir=store.root),
        premise="我要最经典款的剑与魔法", constraints="（无）")
    assert block == _CANON_BLOCK
    assert read_variation(store.root).get("canon") is True
    assert llm.calls == []                              # 正典需求零 LLM


@pytest.mark.asyncio
async def test_resolve_stored_replay(store, tmp_path):
    write_variation(store.root, {"core": "存好的内核", "metaphor": "潮汐"})
    llm = FakeLLM([])
    picks, block = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"), llm=llm,
        prompts=PromptLoader(home_dir=store.root), premise="p", constraints="c")
    assert picks["core"] == "存好的内核" and "存好的内核" in block and llm.calls == []


@pytest.mark.asyncio
async def test_resolve_skips_when_world_not_empty(store, tmp_path):
    store.upsert_world_entries([{"category": "地理", "title": "已知大陆",
                                 "summary": "s", "content": ["c"], "importance": 1}])
    llm = FakeLLM([])
    picks, block = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"), llm=llm,
        prompts=PromptLoader(home_dir=store.root), premise="p", constraints="c")
    assert picks is None and block == "" and llm.calls == []
    assert read_variation(store.root) == {}             # 零落盘


@pytest.mark.asyncio
async def test_resolve_single_survivor_skips_judge(store, tmp_path):
    draft = json.dumps({"candidates": _cands("唯一候选内核")}, ensure_ascii=False)
    llm = FakeLLM([_Resp(draft)])
    picks, _block = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"), llm=llm,
        prompts=PromptLoader(home_dir=store.root), premise="p", constraints="c")
    assert picks["core"] == "唯一候选内核"
    assert len(llm.calls) == 1                          # 单候选不评审
    assert read_variation(store.root)["core"] == "唯一候选内核"


@pytest.mark.asyncio
async def test_resolve_judge_path_with_two_providers(store, tmp_path):
    llm = FakeLLM([_Resp(_DRAFT_OK)])
    judge = FakeLLM([_Resp(_JUDGE_OK)])
    picks, _block = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib", two_providers=True),
        llm=llm, judge_llm=judge, prompts=PromptLoader(home_dir=store.root),
        premise="p", constraints="c")
    assert picks["core"] == "锈蚀吞城的大陆"              # judge 按 chosen=1 选第二套
    assert picks["judge_provider"] == judge.provider.pro
    assert len(judge.calls) == 1


@pytest.mark.asyncio
async def test_resolve_deterministic_fallback_when_judge_unavailable(store, tmp_path):
    """单 provider（无评审）→ _rule_pick 与旧作重合最少；draft 挂 → 四轴池兜底。"""
    llm = FakeLLM([_Resp(_DRAFT_OK)])
    judge = FakeLLM([RuntimeError("judge down")])
    picks, _b = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"),
        llm=llm, judge_llm=judge, prompts=PromptLoader(home_dir=store.root),
        premise="p", constraints="c")
    assert picks["core"] in ("潮汐为凭的世界", "锈蚀吞城的大陆")   # rule_pick 兜底仍出坐标
    # draft 失败 → 四轴池
    info2 = create_project(tmp_path / "lib", "第二本")
    store2 = ProjectStore(info2.path)
    llm2 = FakeLLM([RuntimeError("draft down")])
    picks2, block2 = await resolve_variation(
        store=store2, settings=make_settings(tmp_path / "lib"), llm=llm2,
        prompts=PromptLoader(home_dir=store2.root), premise="p", constraints="c")
    assert set(picks2) == {key for key, _, _ in _VARIATION_AXES}
    assert "【本次创作坐标" in block2


@pytest.mark.asyncio
async def test_resolve_all_banned_falls_to_axes_pool(store, tmp_path):
    banned_cores = json.dumps({"candidates": _cands("债务缠身的世界", "账目通天的大陆")},
                              ensure_ascii=False)
    llm = FakeLLM([_Resp(banned_cores)])
    picks, _b = await resolve_variation(
        store=store, settings=make_settings(tmp_path / "lib"), llm=llm,
        prompts=PromptLoader(home_dir=store.root), premise="p", constraints="c")
    assert set(picks) == {key for key, _, _ in _VARIATION_AXES}   # 全淘汰 → 轴池


# --------------------------------------------------------------------- 注入


def test_readme_premise(store):
    from harness.world_variation import readme_premise

    (store.root / "README.md").write_text(
        "# 坐标书\n\n题材：仙侠\n基调：黑暗\n结局：悲剧\n每章字数：\n", encoding="utf-8")
    assert readme_premise(store.root) == "题材：仙侠；基调：黑暗；结局：悲剧"


def test_cmd_worldcore_stub_notice(store, capsys):
    """未配 provider → /worldcore 如实提示（不炸）。"""
    from harness import repl

    class _Sess:
        def make_components(self):
            return None

    repl._cmd_worldcore(_Sess(), store, "")
    assert "未启动" in capsys.readouterr().out


def test_variation_block_canon_and_empty(store):
    assert variation_block(store.root) == ""
    write_variation(store.root, {"canon": True})
    assert variation_block(store.root) == _CANON_BLOCK
    clear_variation(store.root)
    write_variation(store.root, {"core": "内核", "metaphor": "潮汐"})
    assert "内核" in variation_block(store.root)


# ------------------------------------------------------------------ 提示词钉死

_SEED_DRAFT = (
    "你是世界观创意总监。请为下面这本书一次产出 {{n}} 套**彼此差异极大**的「世界内核」候选，供另一位评审挑选。注意：这一步只出**内核关键词**，不要展开成设定条目。\n"
    "【需求】{{premise}}\n"
    "【限制】{{constraints}}\n"
    "【作者已用过的内核（**不得重复、不得变个说法再来一遍**）】\n"
    "{{used}}\n"
    "【本次禁用套路（**一个都不许出现**）】{{banned}}\n"
    "要求：\n"
    "① 每套必须是一个**自洽的意象系统**（不是四个互不相干的词拼在一起）；\n"
    "② 每套给出：core（一句话内核命题，≤30 字，含主角处境与核心张力）、metaphor（世界本质的取材意象）、medium（超凡力量的介质；故事没有超凡就填「无超凡」）、godstate（神明的存在状态；没有神明就填「不涉及」）、cost（全书核心代价/张力）、why（一句话说明它为什么既避开套路又契合需求）；\n"
    "③ **严禁**再写财务—宗教套路（借债／还债／赎罪券／按揭／什一税／账目／债契／税赋），也严禁元素等级制、几柱神系、通用创世神话；\n"
    "④ 每套都要从**本书特有的处境**倒推；落笔前自问「把它换到另一本书里还成立吗？成立就是太通用，重写」；\n"
    "⑤ 候选之间必须换一个**完全不同的意象世界**，不要只换同义词、同源词。\n"
    "【输出格式】严格 JSON：{\"candidates\": [{\"core\": \"...\", \"metaphor\": \"...\", \"medium\": \"...\", \"godstate\": \"...\", \"cost\": \"...\", \"why\": \"...\"}]}"
)

_SEED_JUDGE = (
    "你是**独立评审**（这些候选不是你写的，请保持挑剔，不必客气）。下面是为同一本书产出的若干套世界内核候选，请选出**最新颖、最不像套路、又最契合需求**的那一套。\n"
    "【需求】{{premise}}\n"
    "【限制】{{constraints}}\n"
    "【作者已用过的内核（重复或近义的一律淘汰）】\n"
    "{{used}}\n"
    "【套路黑名单（命中即淘汰）】{{banned}}\n"
    "【候选（用 index 引用）】\n"
    "{{candidates}}\n"
    "评审标准（每项 0~10）：\n"
    "① novelty 新颖度：是否避开了模型默认套路（财务—宗教式代价、元素等级制、几柱神系、通用创世）；\n"
    "② coherence 自洽度：core／metaphor／medium／godstate／cost 是否构成一套连贯的意象系统；\n"
    "③ fit 契合度：是否真正服务【需求】【限制】；\n"
    "④ writability 可写性：能否撑起长篇（有冲突源、有代价、有成长空间）。\n"
    "命中黑名单、或与「作者已用过」重复/近义的，novelty 直接记 0，并在 note 里写明原因。\n"
    "【输出格式】严格 JSON：{\"reviews\": [{\"index\": 0, \"novelty\": 0, \"coherence\": 0, \"fit\": 0, \"writability\": 0, \"note\": \"...\"}], \"chosen\": 0, \"reason\": \"为什么选它（一句话）\"}"
)


def _builtin(key: str) -> str:
    ref = resources.files("harness.prompts").joinpath("builtin").joinpath(f"{key}.md")
    return ref.read_text(encoding="utf-8")


def test_prompt_draft_seed_verbatim_and_routing():
    text = _builtin("task.auto_world_core_draft")
    assert "\r" not in text
    lines = text.split("\n")
    assert lines[0] == "<!-- tier: standard | temperature: 1.0 | max_tokens: 8192 -->"
    body = "\n".join(lines[1:]).strip("\n")
    # 文档注释（<!-- ... --> 块）之后的第一段正文 = seed 模板逐字
    assert _SEED_DRAFT in body


def test_prompt_judge_seed_verbatim_and_routing():
    text = _builtin("task.auto_world_core_judge")
    assert "\r" not in text
    lines = text.split("\n")
    assert lines[0] == "<!-- tier: pro | temperature: 0.2 | max_tokens: 4096 -->"
    body = "\n".join(lines[1:]).strip("\n")
    assert _SEED_JUDGE in body


def test_prompt_vars_exact():
    import re as _re
    for key, want in (("task.auto_world_core_draft",
                       {"n", "premise", "constraints", "used", "banned"}),
                      ("task.auto_world_core_judge",
                       {"premise", "constraints", "used", "banned", "candidates"})):
        text = _builtin(key)
        found = set(_re.findall(r"\{\{(\w+)\}\}", text))
        assert found == want, f"{key}: {found}"
