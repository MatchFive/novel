"""P23：update_character / delete_character 修卡工具（总设计附录 A.3 本地兑现）。

口径钉死（brief）：
- ``profile`` 只补空缺键、``set_profile`` 逐字段覆盖（同调时 set 后应用、覆盖赢）；
- ``aliases`` 整体替换 / ``add_aliases`` / ``remove_aliases`` 集合增减——全部 as_text
  规整、去空去重保序，落 list[str]；
- ``new_name`` 走 store.rename_character；``role_type`` 升降 protagonist 维护
  protagonists.txt（升加入/降移除/改名迁移）；role_type 校验 ROLE_GUIDES；
- ``delete=true`` 等价删除（连带 protagonists.txt 清理）；
- **名册身份变化**（改名/删除/进出 protagonist）→ 调 reextract_cast 逐章重提记忆；
  ``llm=None`` 跳过重提且观测如实说明；别名/profile 微调不重提（成本，记账）；
- ``delete_character``：逐项隔离、跳过项如实上报；``all=true`` 必须同传
  ``confirm: true`` 否则拒绝（本地无交互确认门，裁定见 brief）；
- 白名单：TOOLS_ALL/_WRITE_FLOW 含两工具（全部 deliverable 集自动获得）。
"""
from __future__ import annotations

import pytest

from harness.characters import ROLE_GUIDES
from harness.projects import create_project
from harness.store.project import ProjectStore
from harness.tools.characters import (
    delete_character,
    register_character_tools,
    update_character,
)

PROFILE_KEYS = ("personality", "motivation", "background", "appearance", "speech_style", "ability")


def _store(tmp_path, title="修卡书") -> ProjectStore:
    info = create_project(tmp_path, title)
    return ProjectStore(info.path)


def _seed_card(store: ProjectStore, *, name="甲", role_type="major", aliases=None,
               profile=None) -> dict:
    card = {"name": name, "role_type": role_type,
            "aliases": list(aliases or []),
            "profile": dict(profile or {k: f"{k}-原值" for k in PROFILE_KEYS}),
            "arc": {"stages": []}}
    store.upsert_character(card)
    return card


def _txt_names(store: ProjectStore) -> list[str]:
    path = store.root / ".harness" / "protagonists.txt"
    if not path.is_file():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ------------------------------------------------------------------ store 层

def test_store_delete_character(tmp_path):
    """delete_character：删卡文件；缺卡抛 FileNotFoundError（同 read 口径）。"""
    store = _store(tmp_path)
    _seed_card(store)
    assert "甲" in {c["name"] for c in store.list_characters()}
    store.delete_character("甲")
    with pytest.raises(FileNotFoundError):
        store.read_character("甲")
    with pytest.raises(FileNotFoundError):
        store.delete_character("甲")        # 重复删 = 缺卡，同口径抛


def test_remove_protagonist(tmp_path):
    """remove_protagonist：滤名保其余；缺文件/缺名不炸（幂等）。"""
    from harness.characters import add_protagonist, remove_protagonist

    root = tmp_path / "proj"
    (root / ".harness").mkdir(parents=True)
    add_protagonist(root, "甲")
    add_protagonist(root, "乙")
    remove_protagonist(root, "甲")
    assert (root / ".harness" / "protagonists.txt").read_text(encoding="utf-8").splitlines() == ["乙"]
    remove_protagonist(root, "不存在")      # 缺名不炸
    remove_protagonist(root, "乙")
    assert (root / ".harness" / "protagonists.txt").read_text(encoding="utf-8").splitlines() == []


# ------------------------------------------------------------------ update_character

async def test_update_profile_fill_and_set(tmp_path):
    """profile 只补空缺；set_profile 按字段覆盖；同调时 set 后应用（覆盖赢）。"""
    store = _store(tmp_path)
    _seed_card(store, profile={"personality": "豪爽", "background": "出身贫寒"})

    res = await update_character(store, None, None, None, {
        "name": "甲",
        "profile": {"personality": "应被忽略", "appearance": "红衣"},   # personality 已有 → 不补
        "set_profile": {"personality": "冷静"},
    })

    assert res.text.startswith("update_character 完成")
    meta = store.read_character("甲")
    assert meta["profile"]["personality"] == "冷静"        # set_profile 覆盖赢
    assert meta["profile"]["background"] == "出身贫寒"      # 未动的保留
    assert meta["profile"]["appearance"] == "红衣"          # profile 只补缺


async def test_update_aliases_normalized(tmp_path):
    """整体替换/add/remove 全部 as_text 规整、去空去重保序。"""
    store = _store(tmp_path)
    _seed_card(store, aliases=["阿甲"])

    await update_character(store, None, None, None,
                           {"name": "甲", "aliases": [" 阿甲 ", "阿甲", "", "  "]})
    assert store.read_character("甲")["aliases"] == ["阿甲"]

    await update_character(store, None, None, None,
                           {"name": "甲", "add_aliases": ["小甲", "小甲", "  "]})
    assert store.read_character("甲")["aliases"] == ["阿甲", "小甲"]

    await update_character(store, None, None, None,
                           {"name": "甲", "remove_aliases": ["阿甲"]})
    assert store.read_character("甲")["aliases"] == ["小甲"]


async def test_update_rename_migrates_and_reextracts(tmp_path, monkeypatch):
    """new_name：旧卡消失新卡在；protagonists.txt 迁移；改名触发名册重提取。"""
    store = _store(tmp_path)
    _seed_card(store, role_type="protagonist")
    calls: list[dict] = []

    async def _fake_reextract(**kw):
        calls.append(kw)
        return [1, 2]

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)

    res = await update_character(store, object(), None, None,
                                 {"name": "甲", "new_name": "甲改名"})

    assert res.text.startswith("update_character 完成")
    with pytest.raises(FileNotFoundError):
        store.read_character("甲")
    meta = store.read_character("甲改名")
    assert meta["role_type"] == "protagonist"
    assert _txt_names(store) == ["甲改名"]                 # 迁移，不残留旧名
    assert len(calls) == 1, "名册身份变化必须触发 reextract_cast"


async def test_update_role_type_maintains_protagonist_list(tmp_path):
    """role_type 升 protagonist → 加名；降出 → 移除。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲", role_type="daily")
    await update_character(store, None, None, None, {"name": "甲", "role_type": "protagonist"})
    assert _txt_names(store) == ["甲"]
    assert store.read_character("甲")["role_type"] == "protagonist"
    await update_character(store, None, None, None, {"name": "甲", "role_type": "major"})
    assert _txt_names(store) == []
    assert store.read_character("甲")["role_type"] == "major"


async def test_update_delete_flag(tmp_path):
    """update_character 的 delete=true 等价删除：连带 protagonists.txt 清理。"""
    store = _store(tmp_path)
    _seed_card(store, role_type="protagonist")

    res = await update_character(store, None, None, None, {"name": "甲", "delete": True})

    assert res.text.startswith("update_character 完成")
    assert "已删除" in res.text.splitlines()[0]
    with pytest.raises(FileNotFoundError):
        store.read_character("甲")
    assert _txt_names(store) == []


async def test_update_arg_errors(tmp_path):
    """缺 name / 卡不存在 / 未知 role_type / 无可应用改动 → 失败（参数），零写盘。"""
    store = _store(tmp_path)
    _seed_card(store)

    res = await update_character(store, None, None, None, {"role_type": "major"})
    assert "失败（参数）" in res.text and "name" in res.text

    res = await update_character(store, None, None, None, {"name": "不存在"})
    assert "失败" in res.text and "不存在" in res.text

    res = await update_character(store, None, None, None,
                                 {"name": "甲", "role_type": "bogus"})
    assert "失败（参数）" in res.text
    assert set(ROLE_GUIDES) and "bogus" in res.text
    assert store.read_character("甲")["role_type"] == "major"   # 拒绝后未动

    res = await update_character(store, None, None, None, {"name": "甲"})
    assert "失败（参数）" in res.text and "没有可应用的改动" in res.text


async def test_update_no_llm_skips_reextract_honestly(tmp_path):
    """llm=None：名册身份变化也不重提，观测如实说明。"""
    store = _store(tmp_path)
    _seed_card(store)
    res = await update_character(store, None, None, None,
                                 {"name": "甲", "new_name": "乙"})
    assert res.text.startswith("update_character 完成")
    assert "未重提取" in res.text


# ------------------------------------------------------------------ delete_character

async def test_delete_isolates_items(tmp_path):
    """names 逐项隔离：存在的删、不存在的跳过并如实上报。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    _seed_card(store, name="乙")

    res = await delete_character(store, None, None, None,
                                 {"names": ["甲", "不存在", "乙"]})

    first = res.text.splitlines()[0]
    assert first.startswith("delete_character 完成")
    assert "跳过" in first and "不存在" in first
    assert store.list_characters() == []


async def test_delete_all_requires_confirm(tmp_path):
    """all=true 无 confirm → 拒绝且一个都不删；confirm:true → 全删。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲")
    _seed_card(store, name="乙")

    res = await delete_character(store, None, None, None, {"all": True})
    assert "拒绝" in res.text and "confirm" in res.text
    assert len(store.list_characters()) == 2               # 一个都没删

    res = await delete_character(store, None, None, None, {"all": True, "confirm": True})
    assert res.text.splitlines()[0].startswith("delete_character 完成")
    assert store.list_characters() == []


async def test_delete_protagonist_cleans_txt_and_reextracts(tmp_path, monkeypatch):
    """删 protagonist 卡：protagonists.txt 同步清；删除触发一次重提取。"""
    store = _store(tmp_path)
    _seed_card(store, name="甲", role_type="protagonist")
    calls: list[dict] = []

    async def _fake_reextract(**kw):
        calls.append(kw)
        return []

    monkeypatch.setattr("harness.characters.reextract_cast", _fake_reextract)
    res = await delete_character(store, object(), None, None, {"name": "甲"})

    assert res.text.splitlines()[0].startswith("delete_character 完成")
    assert _txt_names(store) == []
    assert len(calls) == 1


async def test_delete_no_args(tmp_path):
    """name/names/all 都没给 → 失败（参数）。"""
    store = _store(tmp_path)
    res = await delete_character(store, None, None, None, {})
    assert "失败（参数）" in res.text


# ------------------------------------------------------------------ 白名单 / 签名 / 注册

def test_whitelist_includes_update_delete():
    """TOOLS_ALL/_WRITE_FLOW 含两工具；characters deliverable 可用；只读集不含。"""
    from harness.run.whitelist import _READONLY, _WRITE_FLOW, TOOLS_ALL, allowed_for_run

    assert {"update_character", "delete_character"} <= TOOLS_ALL
    assert {"update_character", "delete_character"} <= _WRITE_FLOW
    assert not {"update_character", "delete_character"} & _READONLY
    assert {"update_character", "delete_character"} <= allowed_for_run(["characters"])


def test_fallback_sigs_registered(tmp_path):
    """_FALLBACK_SIG 兜底签名两行；注册 read_only=False。"""
    from harness.agent.loop import _FALLBACK_SIG
    from harness.tools.registry import ToolRegistry

    assert "update_character" in _FALLBACK_SIG
    assert "delete_character" in _FALLBACK_SIG

    store = _store(tmp_path)
    reg = ToolRegistry(store)
    register_character_tools(reg, store=store, llm=None, prompts=None, settings=None)
    assert "update_character" in reg.names and "delete_character" in reg.names
    assert not reg.is_read_only("update_character")
    assert not reg.is_read_only("delete_character")
