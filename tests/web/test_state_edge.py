"""Task 20：后端边界——坏文件不能让只读 API 500。

三条铁律（brief Step 1）：

- 坏章 → 章节列表跳过该章，不 500；
- state.json 坏 → /state 的 status=None（按无 run 恢复路径处理）；
- 坏角色卡 → 角色视图跳过该卡，计数不炸。

现有容错（``list_chapters(on_error=...)`` / ``RunState.load`` / ``list_characters``）
已覆盖这三条，测试本会直接 PASS——故每条除断言「降级行为」外，还**反向断言**
「坏档确实还在盘上且内容原样」：容错口径是「跳过/按无处理」，绝不删档、不改写。
若坏档不在了，说明测试在真空里跑（vacuously pass），反向断言会把它打红。
"""

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings

GOOD_CHAPTER = "---\nseq: 1\ntitle: 第一章\nstatus: done\n---\n\n正文。\n"
# 坏 YAML（未闭合的 flow 序列）→ split_file 抛 FileFormatError。
BAD_CHAPTER = "---\nseq: [1,\ntitle: 坏章\n---\n\n坏 front-matter。\n"
GOOD_CARD = "---\nname: 好人\nrole_type: protagonist\naliases: [阿好]\n---\n\n# 好人\n"
BAD_CARD = "---\nname: [坏,\nrole_type: major\n---\n\n# 坏卡\n"
BAD_STATE_JSON = "{ 这不是合法 JSON"


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
def project(settings, client):
    """建好一本书，返回项目根目录（预建 chapters/ 便于直接落文件）。"""
    resp = client.post("/api/projects", json={"title": "边界书"})
    assert resp.status_code == 200
    root = settings.library_root / "边界书"
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    return root


def test_broken_chapter_listing_survives(client, project):
    good = project / "chapters" / "0001-good.md"
    good.write_text(GOOD_CHAPTER, encoding="utf-8")
    bad = project / "chapters" / "0002-bad.md"
    bad.write_text(BAD_CHAPTER, encoding="utf-8")

    resp = client.get(f"/api/project/{project.name}/chapters")
    assert resp.status_code == 200
    seqs = [c["seq"] for c in resp.json()["chapters"]]
    assert seqs == [1]  # 坏章跳过，好章仍在

    # state API 同样不炸（坏章按跳过计，chapters=1 而非 2 或 500）。
    resp = client.get(f"/api/project/{project.name}/state")
    assert resp.status_code == 200
    assert resp.json()["counts"]["chapters"] == 1

    # 反向断言：坏档确实在（.bak 备份是 split_file 的既有行为，原文件一字未动）。
    assert bad.exists()
    assert bad.read_text(encoding="utf-8") == BAD_CHAPTER
    assert good.exists()


def test_broken_state_json_survives(client, project):
    state_path = project / ".harness" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(BAD_STATE_JSON, encoding="utf-8")

    resp = client.get(f"/api/project/{project.name}/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] is None and body["brief"] is None
    assert body["running"] is False

    # 反向断言：坏档确实在——容错是「按无状态处理」，不是删档。
    assert state_path.exists()
    assert state_path.read_text(encoding="utf-8") == BAD_STATE_JSON


def test_character_card_bad_frontmatter_survives(client, project):
    cards = project / "characters"
    cards.mkdir(parents=True, exist_ok=True)
    good = cards / "好人.md"
    good.write_text(GOOD_CARD, encoding="utf-8")
    bad = cards / "坏卡.md"
    bad.write_text(BAD_CARD, encoding="utf-8")

    resp = client.get(f"/api/project/{project.name}/characters")
    assert resp.status_code == 200
    body = resp.json()
    counts = body["counts"]
    # 计数不炸：三档全 int（本项目无正文，聚合为空，卡名册剔除坏卡）。
    assert isinstance(counts["carded"], int)
    assert isinstance(counts["pending"], int)
    assert isinstance(counts["walk_on"], int)
    # 坏卡没混进任何一段的 card 字段（card dict 只能是好卡的）。
    for section in ("protagonists", "candidates"):
        for m in body[section]:
            card = m["card"]
            if card is not None:
                assert card.get("name") != "坏卡"

    # state API 不炸。
    resp = client.get(f"/api/project/{project.name}/state")
    assert resp.status_code == 200

    # 反向断言：坏卡确实在且原样。
    assert bad.exists()
    assert bad.read_text(encoding="utf-8") == BAD_CARD
    assert good.exists()
