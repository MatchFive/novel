"""Task 17：draft_character_card + draft_character 工具 + 白名单/签名/注册 + web 起草路由。

口径钉死（brief Step 1 四测 + 1 条路由接线加固）：

- **规整逐字**：缺键补 ""、profile 强制 dict（防模型回字符串）、aliases 强制 list[str]、
  name/role_type 强制入参值——下游 _cast_text/cast_roster 硬依赖此形状（§4.6.2）；
- **路由钉死**：模板首行路由头 pro/0.5/8192（task.auto_character_create），
  调用按 llm.provider.pro + temperature=0.5 + max_tokens=8192 发出；
- **注册/白名单**：draft_character read_only=False，进 TOOLS_ALL 与 _WRITE_FLOW（不进只读集）；
- **web**：POST /characters/draft body {name, role_type, requirement?} → 直接 await 返回
  {"card": ...}（不起后台 task、不 202）；components 缺（未配置 provider）→ 409。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from harness.config import ProviderConfig, Settings
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.store.project import ProjectStore

PROFILE_KEYS = ("personality", "motivation", "background", "appearance", "speech_style", "ability")


class _Resp:
    def __init__(self, text, usage=None):
        self.text = text
        self.usage = usage


class FakeLLM:
    """complete 顺序脚本：_Resp 项为响应；Exception 项直接 raise。"""

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


@pytest.fixture
def fake_llm():
    """工厂 fixture：fake_llm([_Resp(...)]) → FakeLLM。"""
    return FakeLLM


@pytest.fixture
def project(tmp_path):
    info = create_project(tmp_path, "起草书")
    store = ProjectStore(info.path)
    return info, store


def _prompts(info) -> PromptLoader:
    return PromptLoader(home_dir=info.path)


def _payload(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


# ------------------------------------------------------------------ 规整 schema

async def test_draft_normalizes_schema(tmp_path, fake_llm, project):
    """假 llm 回缺键/字符串 profile/非标 aliases/stages → 规整成钉死 schema 的卡 dict。"""
    from harness.characters import draft_character_card

    info, store = project
    llm = fake_llm([_Resp(_payload(
        name="模型乱起的名",
        role_type="daily",                     # 与入参不符 → 强制入参值
        aliases="晚儿",                        # 字符串 → 强制 list[str]
        profile={"personality": "豪爽", "ability": None},   # 缺键补 ""、None 不当 "None"
        arc={"stages": "不是数组"},             # stages 非数组 → []
        extra_key="应被丢弃",                   # 钉死 schema 不留陌生键
    ))])
    card = await draft_character_card(store=store, llm=llm, prompts=_prompts(info),
                                      settings=None, name="苏晚", role_type="major")

    assert set(card.keys()) == {"name", "role_type", "aliases", "profile", "arc"}
    assert card["name"] == "苏晚"
    assert card["role_type"] == "major"
    assert card["aliases"] == ["晚儿"]
    assert set(card["profile"].keys()) == set(PROFILE_KEYS)
    assert card["profile"]["personality"] == "豪爽"
    assert card["profile"]["ability"] == ""          # None → ""，绝不 "None"
    assert card["profile"]["background"] == ""       # 缺键补 ""
    assert card["arc"] == {"stages": []}
    # 路由头钉死：pro 档 / temperature=0.5 / max_tokens=8192
    call = llm.calls[0]
    assert call["model"] == "m-pro"
    assert call["temperature"] == 0.5
    assert call["max_tokens"] == 8192


async def test_draft_forces_name_and_role(tmp_path, fake_llm, project):
    """模型回的名字/定位一律不采信：name/role_type 强制入参值；非法入参响亮失败。"""
    from harness.characters import ROLE_GUIDES, draft_character_card

    info, store = project
    llm = fake_llm([_Resp(_payload(
        name="别的名字", role_type="protagonist",
        aliases=["阿晚"],
        profile={k: f"{k} 内容" for k in PROFILE_KEYS},
        arc={"stages": [{"from_chapter": 1, "to_chapter": 18, "label": "起势",
                         "age": "20", "profile": {"ability": "只覆盖变化字段"}}]},
    ))])
    card = await draft_character_card(store=store, llm=llm, prompts=_prompts(info),
                                      settings=None, name="苏晚", role_type="major",
                                      requirement="需要一个豪爽的师姐")

    assert card["name"] == "苏晚"                 # 模型回「别的名字」→ 强制入参名
    assert card["role_type"] == "major"          # 模型回 protagonist → 强制入参定位
    assert card["aliases"] == ["阿晚"]
    assert card["profile"]["personality"] == "personality 内容"
    # arc.stages 是结构化 dict 列表时**原样保留**（前端逐条展示/可编辑）
    assert card["arc"]["stages"][0]["label"] == "起势"

    # 提示词按本地契约渲染：入参名与 role_label/role_guide 落入正文
    prompt = llm.calls[0]["prompt"]
    assert "苏晚" in prompt
    assert ROLE_GUIDES["major"][0] in prompt      # role_label（重要配角）
    assert ROLE_GUIDES["major"][1][:10] in prompt  # role_guide 注入
    assert "需要一个豪爽的师姐" in prompt

    # 非法入参响亮失败（不渲染、不调 LLM）
    with pytest.raises(ValueError):
        await draft_character_card(store=store, llm=llm, prompts=_prompts(info),
                                   settings=None, name="苏晚", role_type="bogus")
    with pytest.raises(ValueError):
        await draft_character_card(store=store, llm=llm, prompts=_prompts(info),
                                   settings=None, name="  ", role_type="major")
    assert len(llm.calls) == 1                    # 失败路径零 LLM 调用


def test_tool_registered_and_whitelisted(tmp_path):
    """draft_character：注册 read_only=False；TOOLS_ALL/_WRITE_FLOW 含之、只读集不含。"""
    from harness.run.whitelist import _READONLY, _WRITE_FLOW, TOOLS_ALL, allowed_for_run
    from harness.tools.characters import register_character_tools
    from harness.tools.registry import ToolRegistry

    info = create_project(tmp_path, "注册书")
    store = ProjectStore(info.path)
    reg = ToolRegistry(store)
    register_character_tools(reg, store=store, llm=None, prompts=_prompts(info), settings=None)

    assert "draft_character" in reg.names
    assert reg.is_read_only("draft_character") is False

    assert "draft_character" in TOOLS_ALL
    assert "draft_character" in _WRITE_FLOW
    assert "draft_character" not in _READONLY
    assert "draft_character" in allowed_for_run(["draft"])
    assert "draft_character" in allowed_for_run(["outline"])
    assert "draft_character" not in allowed_for_run(["discuss"])   # 写流程集不进只读集

    # 兜底签名（agent loop【可用工具】渲染用）
    from harness.agent.loop import _FALLBACK_SIG
    assert "draft_character(args: {name, role_type, requirement?})" in _FALLBACK_SIG["draft_character"]
    assert "落卡" in _FALLBACK_SIG["draft_character"]


async def test_draft_tool_end_to_end(tmp_path, fake_llm, project):
    """工具层：合法 args → 观测首行确定性 + 卡 JSON 载荷；坏 args → 参数失败观测（零调用）。"""
    from harness.tools.characters import draft_character

    info, store = project
    llm = fake_llm([_Resp(_payload(
        name="x", role_type="x",
        profile={k: "" for k in PROFILE_KEYS}, arc={"stages": []}))])
    result = await draft_character(store, llm, _prompts(info), None,
                                   {"name": "苏晚", "role_type": "supporting"})
    assert result.text.splitlines()[0].startswith("draft_character 完成")
    payload = json.loads("\n".join(result.text.splitlines()[1:]))
    assert payload["name"] == "苏晚"

    # 非法 role_type → 参数失败观测，不烧 LLM
    bad = await draft_character(store, llm, _prompts(info), None,
                                {"name": "苏晚", "role_type": "bogus"})
    assert bad.text.startswith("draft_character 失败（参数）")
    assert len(llm.calls) == 1


# ------------------------------------------------------------------ web 路由

@pytest.fixture
def settings(tmp_path):
    return Settings(
        library_root=tmp_path / "novels",
        providers={},
        default_provider="",
        default_tier="standard",
        web_verify=True,
    )


@pytest.fixture
def client(settings):
    from harness.web.app import create_app

    return TestClient(create_app(settings))


@pytest.fixture
def fx(client):
    resp = client.post("/api/projects", json={"title": "起草路由书"})
    assert resp.status_code == 200
    return resp.json()["id"]


def test_draft_endpoint_409_without_provider(client, fx):
    """components 缺（未配置 provider）→ 409（绝不硬跑、不起幻影调用）。"""
    resp = client.post(f"/api/project/{fx}/characters/draft",
                       json={"name": "苏晚", "role_type": "major"})
    assert resp.status_code == 409
    assert "error" in resp.json()


def test_draft_endpoint_returns_card(client, fx, monkeypatch):
    """接线：body 逐字透传 draft_character_card，直接 await 返回 {"card": ...}（非 202）。"""

    s = client.app.state.settings
    s.providers["dummy"] = ProviderConfig(
        base_url="http://127.0.0.1:9/v1", api_key="x", lite="m", standard="m", pro="m")
    s.default_provider = "dummy"

    seen: dict = {}

    async def fake_draft(*, store, llm, prompts, settings, name, role_type, requirement=""):
        seen.update({"name": name, "role_type": role_type, "requirement": requirement})
        return {"name": name, "role_type": role_type, "aliases": [],
                "profile": {k: "" for k in PROFILE_KEYS}, "arc": {"stages": []}}

    monkeypatch.setattr("harness.characters.draft_character_card", fake_draft)

    resp = client.post(f"/api/project/{fx}/characters/draft",
                       json={"name": "苏晚", "role_type": "major", "requirement": "师姐"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["card"]["name"] == "苏晚"
    assert seen == {"name": "苏晚", "role_type": "major", "requirement": "师姐"}
