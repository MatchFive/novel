"""P24：build_characters 自动建卡司（总设计附录 A.3 本地兑现，brief 见 sdd）。

口径钉死（brief）：
- 规划（plan_cast，harness.characters 核心层）：task.auto_character_cast 一次调用
  → 严格 JSON 数组 [{name, role_type, requirement?}]；非法项（空名/非法 role_type）
  丢弃、全非法 → CastPlanError（分类串同 CharacterDraftError 词表）；按 name 去重保序；
  路由钉死 pro/0.5/4096（模板首行头惯例，不进 WRITING_ROUTING）。
- 去重：既有名册 name+aliases 命中即弃，计数进观测，零起草调用。
- MAX_PROTAGONISTS=2（本地裁定，偏离契约「并入第一位」）：slots = max(0, 2 −
  既有主角数)；新 protagonist 超 slots → 降级 major（卡照常起草、信息无损），
  观测如实说明。
- 并发起草：Semaphore(3) + gather 复用 draft_character_card；单卡失败不拖垮批。
- 确认门：ask_ui_async(ui, ..., multi=True)（edit_by_command 同款）；取消 →
  零落卡、卡 JSON 原样附观测（一张不丢）；批准后逐卡 save_character_card
  （protagonists.txt + 事件广播在其中）+ 一次记忆重提取（llm=None 如实说明）。
- 白名单：TOOLS_ALL/_WRITE_FLOW + characters deliverable；_FALLBACK_SIG 兜底签名。
"""
from __future__ import annotations

import json

import pytest

from harness.config import ProviderConfig
from harness.projects import create_project
from harness.run.approval import ApprovalCancelled, Choice
from harness.store.project import ProjectStore
from harness.tools.characters import build_characters, register_character_tools

PROFILE_KEYS = ("personality", "motivation", "background", "appearance", "speech_style", "ability")


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本：_Resp 项为响应；Exception 项直接 raise。

    build_characters 的脚本顺序 = [规划 1 次] + [每张起草卡各 1 次]——gather 并发但
    协程体内无真实挂起点（complete 立即返回），pop 序 = roster 序，确定性。
    """

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


class FakeUI:
    """ask 同步路径：预置 indices（默认全选）或 cancel；记录 question/options。"""

    def __init__(self, indices=None, cancel=False):
        self._indices = indices
        self._cancel = cancel
        self.questions: list[str] = []
        self.options: list[str] = []

    def ask(self, question, options, multi=False):
        assert multi, "build_characters 确认门必须 multi=True"
        self.questions.append(question)
        self.options = list(options)
        if self._cancel:
            raise ApprovalCancelled()
        indices = self._indices if self._indices is not None else list(range(len(options)))
        return Choice(indices=list(indices))


def _store(tmp_path, title="建卡书") -> ProjectStore:
    info = create_project(tmp_path, title)
    return ProjectStore(info.path)


def _prompts(store: ProjectStore):
    from harness.prompts.loader import PromptLoader

    return PromptLoader(home_dir=store.root)


def _seed_card(store: ProjectStore, *, name="甲", role_type="major", aliases=None) -> dict:
    card = {"name": name, "role_type": role_type,
            "aliases": list(aliases or []),
            "profile": {k: f"{k}-原值" for k in PROFILE_KEYS},
            "arc": {"stages": []}}
    store.upsert_character(card)
    return card


def _roster(entries) -> str:
    return json.dumps(entries, ensure_ascii=False)


def _card_json(name, role_type="major") -> str:
    return json.dumps({"name": name, "role_type": role_type, "aliases": [],
                       "profile": {k: f"{k}-{name}" for k in PROFILE_KEYS},
                       "arc": {"stages": []}}, ensure_ascii=False)


def _txt_names(store: ProjectStore) -> list[str]:
    path = store.root / ".harness" / "protagonists.txt"
    if not path.is_file():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ------------------------------------------------------------------ plan_cast

async def test_plan_cast_parses_validates_dedupes(tmp_path):
    """规划：解析 JSON 数组、校验（空名/非法 role_type 丢弃）、按 name 去重保序。"""
    from harness.characters import plan_cast

    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "主角甲", "role_type": "protagonist", "requirement": "复仇"},
        {"name": "主角甲", "role_type": "major"},            # 重名去重
        {"name": "", "role_type": "major"},                  # 空名丢
        {"name": "乙", "role_type": "bogus"},                # 非法 role 丢
        {"name": "丙", "role_type": "daily"},
    ]))])

    roster, _usage = await plan_cast(store=store, llm=llm,
                                     prompts=_prompts(store), settings=None)

    assert [(r["name"], r["role_type"]) for r in roster] == [
        ("主角甲", "protagonist"), ("丙", "daily")]
    assert roster[0]["requirement"] == "复仇"
    # 路由钉死：pro / 0.5 / 4096
    assert llm.calls[0]["model"] == "m-pro"
    assert llm.calls[0]["temperature"] == 0.5
    assert llm.calls[0]["max_tokens"] == 4096


async def test_plan_cast_failure_raises(tmp_path):
    """规划失败（解析不出有效数组）→ CastPlanError 带分类串。"""
    from harness.characters import CastPlanError, plan_cast

    store = _store(tmp_path)
    llm = FakeLLM([_Resp("not json at all"), _Resp("still not json")])  # 扩额重试第二次

    with pytest.raises(CastPlanError) as ei:
        await plan_cast(store=store, llm=llm, prompts=_prompts(store), settings=None)
    assert ei.value.reason


# ------------------------------------------------------------------ build_characters

async def test_build_approve_all_saves_and_reextracts(tmp_path, monkeypatch):
    """全选批准：落卡 + protagonists.txt 维护 + 一次记忆重提取；首行计数确定性。"""
    store = _store(tmp_path)
    calls: list[dict] = []

    async def _fake_reextract(**kw):
        calls.append(kw)
        return [1]

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "protagonist"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("甲", "protagonist")), _Resp(_card_json("乙", "major"))])

    res = await build_characters(store, llm, _prompts(store), None, FakeUI(), {})

    assert res.text.splitlines()[0].startswith("build_characters 完成")
    assert "落卡 2" in res.text.splitlines()[0]
    assert {c["name"] for c in store.list_characters()} == {"甲", "乙"}
    assert _txt_names(store) == ["甲"]                    # protagonist 落卡并名单
    assert len(calls) == 1, "批准后恰一次记忆重提取"


async def test_build_cancel_saves_nothing(tmp_path):
    """取消（ApprovalCancelled）：零落卡，卡 JSON 原样附观测（一张不丢）。"""
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([{"name": "甲", "role_type": "major"}])),
                   _Resp(_card_json("甲"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(cancel=True), {})

    assert "已取消" in res.text and "零落卡" in res.text
    assert store.list_characters() == []
    assert '"name": "甲"' in res.text                    # 卡 JSON 保留在观测里


async def test_build_partial_approval(tmp_path):
    """多选部分批准：只落选的，未选计数进首行。"""
    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "daily"},
    ])), _Resp(_card_json("甲")), _Resp(_card_json("乙"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(indices=[1]), {})

    assert {c["name"] for c in store.list_characters()} == {"乙"}
    first = res.text.splitlines()[0]
    assert "落卡 1" in first and "未选 1" in first


async def test_build_skips_name_and_alias_dupes(tmp_path):
    """撞名（含别名）去重：计数进首行、零起草调用。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲", aliases=["阿甲"])
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "阿甲", "role_type": "daily"},
        {"name": "乙", "role_type": "major"},
    ])), _Resp(_card_json("乙"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(indices=[0]), {})

    first = res.text.splitlines()[0]
    assert "撞名跳过 2" in first and "甲" in first and "阿甲" in first
    assert len(llm.calls) == 2, "规划 1 + 起草 1（两张撞名未起草）"
    assert "乙" in {c["name"] for c in store.list_characters()}


async def test_build_no_fresh_is_noop(tmp_path):
    """规划项全部撞名 → 完成观测「无需新建」，零起草调用。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    llm = FakeLLM([_Resp(_roster([{"name": "甲", "role_type": "major"}]))])

    res = await build_characters(store, llm, _prompts(store), None, FakeUI(), {})

    assert "无需新建" in res.text.splitlines()[0]
    assert len(llm.calls) == 1


async def test_build_demotes_excess_protagonists(tmp_path):
    """主角名额：既有 1 主角 + 规划 2 主角 → 1 保留 + 1 降级 major（观测说明）。"""
    from harness.characters import add_protagonist

    store = _store(tmp_path)
    _seed_card(store, name="旧主角", role_type="protagonist")
    add_protagonist(store.root, "旧主角")      # save_character_card 的职责，测试显式 seed
    llm = FakeLLM([_Resp(_roster([
        {"name": "新甲", "role_type": "protagonist"},
        {"name": "新乙", "role_type": "protagonist"},
    ])), _Resp(_card_json("新甲", "protagonist")), _Resp(_card_json("新乙", "major"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(indices=[0, 1]), {})

    assert "降级" in res.text
    assert store.read_character("新乙")["role_type"] == "major"
    assert _txt_names(store) == ["旧主角", "新甲"]        # 名额 1 → 只进一张主角
    assert res.text.splitlines()[0].startswith("build_characters 完成")


async def test_build_demotes_when_no_slots(tmp_path):
    """既有主角已满 2 → 规划的新主角全部降级 major。"""
    from harness.characters import add_protagonist

    store = _store(tmp_path)
    _seed_card(store, name="旧甲", role_type="protagonist")
    _seed_card(store, name="旧乙", role_type="protagonist")
    add_protagonist(store.root, "旧甲")
    add_protagonist(store.root, "旧乙")
    llm = FakeLLM([_Resp(_roster([{"name": "新甲", "role_type": "protagonist"}])),
                   _Resp(_card_json("新甲", "major"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(indices=[0]), {})

    assert "降级" in res.text
    assert store.read_character("新甲")["role_type"] == "major"
    assert _txt_names(store) == ["旧甲", "旧乙"]


async def test_build_plan_failure_observed(tmp_path):
    """规划失败：失败观测带分类串，零起草调用。"""
    store = _store(tmp_path)
    llm = FakeLLM([_Resp("garbage"), _Resp("still garbage")])  # 扩额重试第二次

    res = await build_characters(store, llm, _prompts(store), None, FakeUI(), {})

    assert res.text.startswith("build_characters 失败（")
    assert len(llm.calls) == 2, "规划 1 + 扩额重试 1（解析失败重试是既定行为）；零起草"


async def test_build_requires_llm(tmp_path):
    """llm=None：参数失败（规划必须调 LLM）。"""
    store = _store(tmp_path)

    res = await build_characters(store, None, _prompts(store), None, FakeUI(), {})

    assert "失败（参数）" in res.text


async def test_build_draft_failure_isolated(tmp_path):
    """单卡起草失败不拖垮批：失败卡进清单、其余正常确认落卡。"""
    from harness.characters import CharacterDraftError

    store = _store(tmp_path)
    llm = FakeLLM([_Resp(_roster([
        {"name": "甲", "role_type": "major"},
        {"name": "乙", "role_type": "major"},
    ])), CharacterDraftError("bad_json"), _Resp(_card_json("乙"))])

    res = await build_characters(store, llm, _prompts(store), None,
                                 FakeUI(indices=[0]), {})

    first = res.text.splitlines()[0]
    assert "起草失败 1" in first and "甲" in first
    assert {c["name"] for c in store.list_characters()} == {"乙"}


# ------------------------------------------------------------------ 白名单 / 签名 / 注册

def test_whitelist_includes_build():
    """TOOLS_ALL/_WRITE_FLOW/characters deliverable 含 build_characters；只读集不含。"""
    from harness.run.whitelist import _READONLY, _WRITE_FLOW, TOOLS_ALL, allowed_for_run

    assert "build_characters" in TOOLS_ALL
    assert "build_characters" in _WRITE_FLOW
    assert "build_characters" not in _READONLY
    assert "build_characters" in allowed_for_run(["characters"])


def test_fallback_sig_and_registration(tmp_path):
    """_FALLBACK_SIG 兜底签名；注册 read_only=False；ui 可注入。"""
    from harness.agent.loop import _FALLBACK_SIG
    from harness.tools.registry import ToolRegistry

    assert "build_characters" in _FALLBACK_SIG

    store = _store(tmp_path)
    reg = ToolRegistry(store)
    register_character_tools(reg, store=store, llm=None, prompts=None,
                             settings=None, ui=FakeUI())
    assert "build_characters" in reg.names
    assert not reg.is_read_only("build_characters")
