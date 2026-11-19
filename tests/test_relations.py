"""P33：关系网自动生成（A9 收官，brief 见 sdd/2026-11-19-p33-relationship-graph）。

口径钉死（brief）：
- relations.md：项目根独立文档；front-matter 规范结构 + 人类可读正文；
  read_relations 缺文件 → []；remove/rename 返回条数、零变更不重写。
- normalize_relations 纯函数：name+alias 解析成规范卡名；未命中/自配对/空 relation
  丢弃（计数）；(from,to,relation) 精确三元去重保序。
- generate_relationship_graph：cast_roster + aliases → task.character_relations
  （路由钉死 standard/0.6/4096）；<2 张卡 ValueError 零 LLM；结构化失败
  CharacterDraftError（同分类串词表）。
- build_characters 接线：落卡+记忆重提后生成 → 第二道确认门（取消=卡照留、关系
  零写盘、JSON 附观测）→ write_relations 整写；生成失败不拖垮 build（观测带分类串）。
- 级联：delete 清理相关条；update 改名改写端点。
"""
from __future__ import annotations

import json
import re
from importlib import resources

import pytest

from harness.characters import (
    CharacterDraftError,
    generate_relationship_graph,
    normalize_relations,
)
from harness.config import ProviderConfig
from harness.projects import create_project
from harness.run.approval import ApprovalCancelled, Choice
from harness.store.project import ProjectStore
from harness.tools.characters import delete_character, update_character

PROFILE_KEYS = ("personality", "motivation", "background", "appearance", "speech_style", "ability")


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本（同 test_character_build 的口径）：_Resp 为响应，Exception 抛。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.provider = ProviderConfig(
            base_url="https://api.test", api_key="k",
            lite="m-lite", standard="m-std", pro="m-pro")

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append({"model": model, "temperature": temperature,
                           "max_tokens": max_tokens,
                           "prompt": messages[-1]["content"]})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedUI:
    """多道确认门：按 ask 顺序 pop 脚本项（Choice=答、ApprovalCancelled=取消）。"""

    def __init__(self, script):
        self._script = list(script)
        self.questions: list[str] = []
        self.options: list[list[str]] = []

    def ask(self, question, options, multi=False):
        assert multi, "build_characters 确认门必须 multi=True"
        self.questions.append(question)
        self.options.append(list(options))
        item = self._script.pop(0)
        if isinstance(item, ApprovalCancelled):
            raise item
        return item


def _store(tmp_path, title="关系网书") -> ProjectStore:
    info = create_project(tmp_path, title)
    return ProjectStore(info.path)


def _prompts(store: ProjectStore):
    from harness.prompts.loader import PromptLoader

    return PromptLoader(home_dir=store.root)


def _seed_card(store: ProjectStore, *, name="甲", role_type="major",
               aliases=None) -> dict:
    card = {"name": name, "role_type": role_type,
            "aliases": list(aliases or []),
            "profile": {k: f"{k}-{name}" for k in PROFILE_KEYS},
            "arc": {"stages": []}}
    store.upsert_character(card)
    return card


_RELS = [
    {"from": "甲", "to": "乙", "relation": "盟友", "description": "过命的交情"},
    {"from": "乙", "to": "丙", "relation": "师徒", "description": "启蒙恩师"},
    {"from": "丙", "to": "甲", "relation": "利益冲突", "description": "争同一块地"},
]


# ------------------------------------------------------------------ store

def test_store_relations_roundtrip(tmp_path):
    """write_relations → read_relations 逐字段往返；文件落在项目根 relations.md。"""
    store = _store(tmp_path)
    path = store.write_relations(_RELS)
    assert path == store.root / "relations.md"
    assert store.read_relations() == _RELS


def test_store_read_relations_missing_is_empty(tmp_path):
    """缺文件 → []（不 raise）。"""
    store = _store(tmp_path)
    assert store.read_relations() == []


def test_store_remove_relations_for(tmp_path):
    """remove_relations_for：端点命中即删，返回条数；零命中不动文件。"""
    store = _store(tmp_path)
    store.write_relations(_RELS)
    assert store.remove_relations_for("乙") == 2
    assert [r["from"] for r in store.read_relations()] == ["丙"]
    before = (store.root / "relations.md").read_text(encoding="utf-8")
    assert store.remove_relations_for("不存在") == 0
    assert (store.root / "relations.md").read_text(encoding="utf-8") == before


def test_store_rename_relations(tmp_path):
    """rename_relations：端点改写，返回条数；零命中不动文件。"""
    store = _store(tmp_path)
    store.write_relations(_RELS)
    assert store.rename_relations("甲", "甲二世") == 2
    rels = store.read_relations()
    assert [(r["from"], r["to"]) for r in rels] == [
        ("甲二世", "乙"), ("乙", "丙"), ("丙", "甲二世")]
    before = (store.root / "relations.md").read_text(encoding="utf-8")
    assert store.rename_relations("不存在", "x") == 0
    assert (store.root / "relations.md").read_text(encoding="utf-8") == before


# ------------------------------------------------------------------ normalize_relations

def test_normalize_resolves_alias_and_counts_drops():
    """name+alias 解析成规范卡名；未命中/自配对/空 relation 丢弃计数；三元去重保序。"""
    resolve = {"甲": "甲", "阿甲": "甲", "乙": "乙"}
    raw = [
        {"from": "阿甲", "to": "乙", "relation": "盟友", "description": "d1"},  # 别名→甲
        {"from": "甲", "to": "乙", "relation": "盟友", "description": "d1"},    # 三元重复→去重
        {"from": "丙", "to": "乙", "relation": "盟友"},                          # 未命中→丢
        {"from": "甲", "to": "阿甲", "relation": "盟友"},                        # 解析后自配对→丢
        {"from": "甲", "to": "乙", "relation": "  "},                            # 空 relation→丢
        {"from": "乙", "to": "甲", "relation": "宿敌"},                          # 反向不同三元→保留
    ]
    rels, dropped = normalize_relations(raw, resolve)
    assert rels == [
        {"from": "甲", "to": "乙", "relation": "盟友", "description": "d1"},
        {"from": "乙", "to": "甲", "relation": "宿敌", "description": ""},
    ]
    assert dropped == 4


# ------------------------------------------------------------------ generate_relationship_graph

async def test_generate_requires_two_cards_zero_llm(tmp_path):
    """<2 张卡 → ValueError（seed 400 口径），零 LLM 调用。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    llm = FakeLLM([])

    with pytest.raises(ValueError, match="至少需要 2 个角色"):
        await generate_relationship_graph(store=store, llm=llm,
                                          prompts=_prompts(store), settings=None)
    assert llm.calls == []


async def test_generate_routing_parse_and_alias_resolution(tmp_path):
    """路由钉死 standard/0.6/4096；prompt 含卡司文本；别名解析+丢弃计数。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲", aliases=["阿甲"])
    _seed_card(store, name="乙")
    payload = {"relations": [
        {"from": "甲", "to": "乙", "relation": "盟友", "description": "d"},
        {"from": "阿甲", "to": "乙", "relation": "宿敌", "description": "d"},
        {"from": "丙", "to": "乙", "relation": "家人"},
    ]}
    llm = FakeLLM([_Resp(json.dumps(payload, ensure_ascii=False))])

    rels, _usage, dropped = await generate_relationship_graph(
        store=store, llm=llm, prompts=_prompts(store), settings=None)

    assert llm.calls[0]["model"] == "m-std"
    assert llm.calls[0]["temperature"] == 0.6
    assert llm.calls[0]["max_tokens"] == 4096
    assert "【甲】" in llm.calls[0]["prompt"] and "【乙】" in llm.calls[0]["prompt"]
    assert rels == [
        {"from": "甲", "to": "乙", "relation": "盟友", "description": "d"},
        {"from": "甲", "to": "乙", "relation": "宿敌", "description": "d"},
    ]
    assert dropped == 1


async def test_generate_bad_json_raises_classified(tmp_path):
    """结构化失败 → CharacterDraftError（bad_json 分类串，同起草词表）。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    _seed_card(store, name="乙")
    llm = FakeLLM([_Resp("garbage"), _Resp("still garbage")])  # 扩额重试第二次

    with pytest.raises(CharacterDraftError) as ei:
        await generate_relationship_graph(store=store, llm=llm,
                                          prompts=_prompts(store), settings=None)
    assert ei.value.reason == "bad_json"


# ------------------------------------------------------------------ build_characters 接线

def _build(llm, ui, store):
    from harness.tools.characters import build_characters

    return build_characters(store, llm, _prompts(store), None, ui, {})


def _roster(entries) -> str:
    return json.dumps(entries, ensure_ascii=False)


def _card_json(name, role_type="major") -> str:
    return json.dumps({"name": name, "role_type": role_type, "aliases": [],
                       "profile": {k: f"{k}-{name}" for k in PROFILE_KEYS},
                       "arc": {"stages": []}}, ensure_ascii=False)


_RELS_JSON = json.dumps({"relations": [
    {"from": "甲", "to": "乙", "relation": "盟友", "description": "过命的交情"},
    {"from": "乙", "to": "甲", "relation": "宿敌", "description": "针锋相对"},
]}, ensure_ascii=False)


async def test_build_writes_relations_after_second_gate(tmp_path, monkeypatch):
    """两道门全批准：落卡 + relations.md 整写；首行带关系网计数；第二道门提重写。"""
    async def _fake_reextract(**kw):
        return [1]

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙")), _Resp(_RELS_JSON)])
    ui = ScriptedUI([Choice(indices=[0, 1]), Choice(indices=[0, 1])])

    res = await _build(llm, ui, store)

    assert len(ui.questions) == 2, "卡确认门 + 关系网确认门"
    assert "重写 relations.md" in ui.questions[1]
    assert store.read_relations() == json.loads(_RELS_JSON)["relations"]
    first = res.text.splitlines()[0]
    assert first.startswith("build_characters 完成")
    assert "关系网 2 条" in first and "写入 relations.md" in first


async def test_build_relation_gate_cancel_keeps_cards(tmp_path, monkeypatch):
    """关系网门取消：卡照留（已落），关系零写盘，JSON 附观测。"""
    async def _fake_reextract(**kw):
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙")), _Resp(_RELS_JSON)])
    ui = ScriptedUI([Choice(indices=[0, 1]), ApprovalCancelled()])

    res = await _build(llm, ui, store)

    assert {c["name"] for c in store.list_characters()} == {"甲", "乙"}
    assert not (store.root / "relations.md").is_file(), "取消 = 关系零写盘"
    assert "关系网未写入（已取消）" in res.text.splitlines()[0]
    assert '"from"' in res.text, "关系 JSON 原样附观测"


async def test_build_relation_graph_failure_does_not_break_build(tmp_path, monkeypatch):
    """关系网生成失败不拖垮 build：卡已落，观测带分类串。"""
    async def _fake_reextract(**kw):
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙")),
        _Resp("garbage"), _Resp("still garbage")])
    ui = ScriptedUI([Choice(indices=[0, 1])])

    res = await _build(llm, ui, store)

    assert {c["name"] for c in store.list_characters()} == {"甲", "乙"}
    assert not (store.root / "relations.md").is_file()
    assert "关系网生成失败（bad_json）" in res.text.splitlines()[0]


async def test_build_relation_graph_api_error_observed(tmp_path, monkeypatch):
    """关系网生成遇非 LLMError 异常：观测 api:error 分类，build 不崩。"""
    async def _fake_reextract(**kw):
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙")), RuntimeError("boom")])
    ui = ScriptedUI([Choice(indices=[0, 1])])

    res = await _build(llm, ui, store)

    assert {c["name"] for c in store.list_characters()} == {"甲", "乙"}
    assert "关系网生成失败（api:error）" in res.text.splitlines()[0]


async def test_build_relation_skipped_below_two_cards(tmp_path, monkeypatch):
    """全名册 <2 张卡：跳过生成（零关系 LLM 调用），观测说明。"""
    async def _fake_reextract(**kw):
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙"))])
    ui = ScriptedUI([Choice(indices=[0])])   # 只批准一张 → 全名册 1 张

    res = await _build(llm, ui, store)

    assert "关系网跳过（不足 2 位角色）" in res.text.splitlines()[0]
    assert len(llm.calls) == 3, "规划 1 + 起草 2，零关系网调用"


# ------------------------------------------------------------------ delete / rename 级联

async def test_delete_character_cleans_relations(tmp_path):
    """删卡级联：relations.md 里相关条目清掉，条数进观测。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    _seed_card(store, name="乙")
    _seed_card(store, name="丙")
    store.write_relations(_RELS)

    res = await delete_character(store, None, None, None, {"name": "乙"})

    assert store.read_relations() == [dict(_RELS[2])]
    assert "关系网清理 2 条" in res.text


async def test_update_rename_rewrites_relations(tmp_path):
    """改名级联：关系端点跟随改写。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    _seed_card(store, name="乙")
    store.write_relations([_RELS[0]])

    res = await update_character(store, None, None, None,
                                 {"name": "甲", "new_name": "甲二世"})

    rels = store.read_relations()
    assert rels[0]["from"] == "甲二世" and rels[0]["to"] == "乙"
    assert "关系网改写 1 条" in res.text


# ------------------------------------------------------------------ 提示词契约

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")
_SEED_BODY = (
    "请基于以下角色卡生成角色关系网：列出有代表性的角色间关系"
    "（盟友/宿敌/师徒/恋人/家人/利益冲突等），每条含双方、关系类型、一句话描述。"
)


def test_relations_prompt_var_set_is_local_contract():
    from harness.prompts.loader import PromptLoader

    text = PromptLoader().get("task.character_relations")
    assert set(_VAR_RE.findall(text)) == {"cast_cards"}


def test_relations_prompt_seed_body_verbatim():
    """seed 底本（web_dream prompts.json「角色关系网生成」）逐字保留。"""
    from harness.prompts.loader import PromptLoader

    text = PromptLoader().get("task.character_relations")
    assert _SEED_BODY in text
    assert "【角色卡】" in text and "【输出格式】" in text
    assert '"relations": [{"from": "角色A", "to": "角色B", "relation": "盟友"' in text


def test_relations_prompt_routing_header_and_lf():
    ref = (resources.files("harness.prompts").joinpath("builtin")
           .joinpath("task.character_relations.md"))
    data = ref.read_bytes()
    assert b"\r\n" not in data
    assert data.split(b"\n", 1)[0] == b"<!-- tier: standard | temperature: 0.6 | max_tokens: 4096 -->"
