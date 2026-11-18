"""Task 18：落卡 —— save_character_card + characters_built + 记忆重提取 + PUT 端点。

口径钉死（brief Step 1 四测）：

- **save_character_card**：``store.upsert_character(card)`` →
  ``events.append("characters_built", {"count": 1, "names": [card["name"]]})``
  （payload 逐字）→ return path；``role_type=="protagonist"`` 的卡落盘时名并入
  ``<root>/.harness/protagonists.txt``（写逻辑收进 ``add_protagonist`` 单一函数，
  主角源两读一写）；
- **落盘回读**：profile 嵌套 dict 往返仍是 dict（§4.6.2 schema）；
- **reextract_cast**：对已有章逐章 ``reextract_chapter``（幂等），返回重提章号列表；
  list_chapters 已定位到的 doc 原样传 ``doc=``（不按 seq 再 glob——手写章文件名
  前缀与 fm 章号常不一致）；
- **PUT /characters/{name}**：body = 卡 dict（§4.6.2）→ save_character_card →
  202 ``{"saved": true, "path": str, "reextract_started": [seq...]}``；reextract 用
  ``asyncio.create_task`` 后台跑（同 loop，禁 asyncio.run）。
"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from harness.config import ProviderConfig, Settings
from harness.llm.client import CompletionUsage
from harness.memory.store import MemoryStore
from harness.projects import create_project
from harness.prompts.loader import PromptLoader
from harness.run.events import EventLog
from harness.store.project import ChapterDoc, ProjectStore

PROFILE_KEYS = ("personality", "motivation", "background",
                "appearance", "speech_style", "ability")


class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage = CompletionUsage(prompt_tokens=1, completion_tokens=1)


class FakeLLM:
    """complete 顺序脚本：str 项为响应文本；Exception 项直接 raise。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def complete(self, messages, *, model, temperature, max_tokens):
        self.calls.append(messages[-1]["content"])
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


@pytest.fixture
def fake_llm():
    """工厂 fixture：fake_llm(["..."]) → FakeLLM。"""
    return FakeLLM


@pytest.fixture
def project(tmp_path):
    info = create_project(tmp_path, "落卡书")
    return info, ProjectStore(info.path)


def _card(name="苏晚", role_type="major") -> dict:
    return {"name": name, "role_type": role_type, "aliases": [],
            "profile": {k: "" for k in PROFILE_KEYS}, "arc": {"stages": []}}


def _extract_payload() -> str:
    """五键全空的合法提取 payload（零事件也能过解析层）。"""
    return json.dumps({"events": [], "character_events": [], "info_updates": [],
                       "promise_updates": [],
                       "state_updates": {"characters": [], "world": []}},
                      ensure_ascii=False)


def _make_settings() -> Settings:
    return Settings(
        library_root="x",
        providers={"test": ProviderConfig(base_url="https://api.test", api_key="k",
                                          lite="m", standard="m", pro="m")},
        default_provider="test",
        default_tier="standard",
        web_verify=True,
    )


# ------------------------------------------------------------------ save_character_card

def test_save_emits_characters_built(tmp_path, project):
    """事件 payload 逐字 {"count": 1, "names": ["苏晚"]}；非 protagonist 卡不碰主角名单。"""
    from harness.characters import save_character_card

    info, store = project
    events = EventLog(info.path)
    path = save_character_card(store, events, _card())

    assert path == info.path / "characters" / "苏晚.md"
    assert path.exists()
    tail = events.tail(1)
    assert tail[0]["kind"] == "characters_built"
    assert tail[0]["payload"] == {"count": 1, "names": ["苏晚"]}
    # major 卡不是主角源：protagonists.txt 不产生
    assert not (info.path / ".harness" / "protagonists.txt").exists()


def test_saved_card_roundtrip_profile_dict(tmp_path, project):
    """落盘回读 profile 仍是嵌套 dict（§4.6.2 schema 往返）；protagonist 卡名入
    protagonists.txt，重复落卡不重复（add_protagonist 幂等）。"""
    from harness.characters import save_character_card

    info, store = project
    profile = {k: f"{k} 内容" for k in PROFILE_KEYS}
    card = {"name": "苏晚", "role_type": "protagonist", "aliases": ["晚儿"],
            "profile": profile,
            "arc": {"stages": [{"from_chapter": 1, "to_chapter": 10, "label": "起势"}]}}
    save_character_card(store, EventLog(info.path), card)

    meta = store.read_character("苏晚")
    assert isinstance(meta["profile"], dict)          # 嵌套 dict 往返不塌成字符串
    assert meta["profile"]["personality"] == "personality 内容"
    assert meta["profile"]["ability"] == "ability 内容"
    assert meta["arc"]["stages"][0]["label"] == "起势"
    assert meta["aliases"] == ["晚儿"]

    # 主角源两读之一的 protagonists.txt 被并入（第二个写入方，单一写函数）
    txt = info.path / ".harness" / "protagonists.txt"
    assert txt.exists()
    lines = [ln.strip() for ln in txt.read_text(encoding="utf-8").splitlines()]
    assert lines.count("苏晚") == 1
    # 幂等：再落一次不追加重复名
    save_character_card(store, EventLog(info.path), card)
    lines = [ln.strip() for ln in txt.read_text(encoding="utf-8").splitlines()]
    assert lines.count("苏晚") == 1


# ------------------------------------------------------------------ reextract_cast

async def test_reextract_cast_reextracts_existing_chapters(tmp_path, fake_llm, project):
    """已有两章 + 一张 protagonist 卡 → 逐章重提取（幂等），返回 [1, 2]；
    名册现取（卡一落名册即生效）：提取 prompt 里带刚落卡的 profile。"""
    from harness.characters import reextract_cast, save_character_card

    info, store = project
    save_character_card(store, EventLog(info.path),
                        {"name": "苏晚", "role_type": "protagonist", "aliases": [],
                         "profile": {"personality": "豪爽", "motivation": "复仇",
                                     "background": "", "appearance": "",
                                     "speech_style": "", "ability": "刀法"},
                         "arc": {"stages": []}})
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "题一"}, body="苏晚拔刀。"))
    store.write_chapter(ChapterDoc(seq=2, meta={"seq": 2, "title": "题二"}, body="风起。"))
    (info.path / "memory").mkdir(exist_ok=True)
    (info.path / "memory" / "state.md").write_text(
        "# 现状快照\n\n## 角色现状\n\n## 世界现状\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".harness" / "prompts").mkdir(parents=True)

    llm = fake_llm([_extract_payload(), _extract_payload()])
    prompts = PromptLoader(home_dir=home)
    seqs = await reextract_cast(store=store, mem=MemoryStore(info.path), llm=llm,
                                prompts=prompts, settings=_make_settings())

    assert seqs == [1, 2]
    assert len(llm.calls) == 2
    # 卡一落名册即生效（§8.4）：两章的 cast_cards 渲染都带刚落的 protagonist 卡
    for prompt in llm.calls:
        assert "【苏晚】(protagonist)" in prompt
        assert "豪爽" in prompt
    # 逐章幂等：同卡再跑一次不重复条目
    again = await reextract_cast(store=store, mem=MemoryStore(info.path),
                                 llm=fake_llm([_extract_payload(), _extract_payload()]),
                                 prompts=prompts, settings=_make_settings())
    assert again == [1, 2]


# ------------------------------------------------------------------ PUT 端点

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


def test_put_endpoint(tmp_path, client, monkeypatch):
    """PUT body = 卡 dict → 202 逐字 {"saved": true, "path", "reextract_started"}；
    卡真落盘、characters_built 事件已发、protagonist 名入 protagonists.txt；
    reextract_cast 被 create_task 后台调起（spy 实证）。"""
    s = client.app.state.settings
    s.providers["dummy"] = ProviderConfig(
        base_url="http://127.0.0.1:9/v1", api_key="x", lite="m", standard="m", pro="m")
    s.default_provider = "dummy"
    resp = client.post("/api/projects", json={"title": "落卡路由书"})
    assert resp.status_code == 200
    pid = resp.json()["id"]
    root = s.library_root / pid

    # 一章已存在 → reextract_started 含其 seq
    ProjectStore(root).write_chapter(
        ChapterDoc(seq=1, meta={"seq": 1, "title": "题一"}, body="苏晚拔刀。"))

    called = threading.Event()

    async def fake_reextract_cast(**kwargs):
        called.set()
        return [1]

    monkeypatch.setattr("harness.characters.reextract_cast", fake_reextract_cast)

    card = {"name": "苏晚", "role_type": "protagonist", "aliases": ["晚儿"],
            "profile": {k: f"{k}内容" for k in PROFILE_KEYS}, "arc": {"stages": []}}
    resp = client.put(f"/api/project/{pid}/characters/苏晚", json=card)

    assert resp.status_code == 202
    assert resp.json() == {"saved": True,
                           "path": str(root / "characters" / "苏晚.md"),
                           "reextract_started": [1]}
    # 卡真落盘 + 事件逐字 payload + 主角名并入
    assert (root / "characters" / "苏晚.md").exists()
    assert any(e["kind"] == "characters_built"
               and e["payload"] == {"count": 1, "names": ["苏晚"]}
               for e in EventLog(root).tail(10))
    assert "苏晚" in (root / ".harness" / "protagonists.txt").read_text(encoding="utf-8")
    # 后台重提取被 asyncio.create_task 调起（TestClient 的 loop 在另一线程）
    assert called.wait(timeout=10)


def test_put_endpoint_name_mismatch_400(client):
    """Task 21 follow-up ④：body name 与路径名不一致 → 400（写明两名字），卡不落盘。"""
    resp = client.post("/api/projects", json={"title": "名校验"})
    assert resp.status_code == 200
    pid = resp.json()["id"]
    root = client.app.state.settings.library_root / pid

    card = _card()
    card["name"] = "苏晚晴"
    resp = client.put(f"/api/project/{pid}/characters/苏晚", json=card)

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert "苏晚" in error and "苏晚晴" in error      # 两个名字都写进错误里
    assert not (root / "characters" / "苏晚.md").exists()
    assert not (root / "characters" / "苏晚晴.md").exists()


def test_put_endpoint_without_provider(client, monkeypatch):
    """components 缺（未配置 provider）→ 卡照落（落卡不依赖 LLM），
    reextract_started 如实回 []，不起后台 task。"""
    resp = client.post("/api/projects", json={"title": "无provider落卡"})
    assert resp.status_code == 200
    pid = resp.json()["id"]
    root = client.app.state.settings.library_root / pid

    called = threading.Event()

    async def fake_reextract_cast(**kwargs):
        called.set()
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", fake_reextract_cast)

    resp = client.put(f"/api/project/{pid}/characters/苏晚", json=_card())

    assert resp.status_code == 202
    assert resp.json() == {"saved": True,
                           "path": str(root / "characters" / "苏晚.md"),
                           "reextract_started": []}
    assert (root / "characters" / "苏晚.md").exists()
    assert not called.wait(timeout=1)
