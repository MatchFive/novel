"""Task 6：七视图只读 API（readers + 路由）。

fixture 项目造全套：arcs+plots（两卷）、3 章（第 2 章带审核快照、units 含无卡主要角色
「配角乙」、第 3 章大量无角色名拍稀释出龙套「龙套丙」）、2 条 issue（1 条 resolved）、
1 份章记忆（角色经历为空 → 诚实文案 empty_dimensions）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings


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
def fx(client, settings):
    """建全套 fixture 项目，返回项目 id。"""
    from harness.memory.store import MemoryStore
    from harness.run.issues import IssuesStore
    from harness.store.project import ChapterDoc, ProjectStore

    resp = client.post("/api/projects", json={"title": "视图书"})
    assert resp.status_code == 200
    pid = resp.json()["id"]
    root = settings.library_root / pid
    store = ProjectStore(root)

    # 卷骨架 + 两卷情节（第 1 卷两条、第 2 卷一条）
    store.upsert_arcs([{"vol": 1, "title": "第一卷"}, {"vol": 2, "title": "第二卷"}],
                      planned_volume_count=3)
    store.upsert_plots(1, [{"seq": 1, "title": "开篇", "status": "done", "summary": "上山"}])
    store.upsert_plots(1, [{"seq": 2, "title": "试炼", "status": "planned"}])
    store.upsert_plots(2, [{"seq": 3, "title": "远行", "status": "planned"}])

    # 主角有卡；配角乙无卡（主要角色 → blocked_by）；龙套丙 share < 阈值 → 龙套
    store.upsert_character({"name": "主角甲", "role_type": "protagonist", "aliases": ["小甲"]})

    store.write_chapter(ChapterDoc(seq=1, meta={
        "seq": 1, "title": "第一章", "status": "done", "plot_id": 1, "word_count": 2000,
        "units": [
            {"characters": ["主角甲"]},
            {"characters": ["主角甲", "配角乙"]},
            {"characters": ["配角乙"]},
            {"characters": ["众新弟子"]},          # 集合名词：整体剔除
            {"text": "无角色名的一拍"},             # 无角色名：计入总 beats 分母
        ],
    }, body="第一章正文"))
    store.write_chapter(ChapterDoc(seq=2, meta={
        "seq": 2, "title": "第二章", "status": "draft", "plot_id": 2, "word_count": 1000,
        "units": [
            {"characters": ["主角甲"]},
            {"characters": ["主角甲"]},
            {"narration": True},
        ],
    }, body="第二章正文"))
    # 第 3 章：30 拍无角色名 + 1 拍龙套丙 → 总 beats=39，龙套丙 share=1/39<0.03
    store.write_chapter(ChapterDoc(seq=3, meta={
        "seq": 3, "title": "第三章", "status": "draft", "plot_id": 3,
        "units": [{"text": f"拍{i}"} for i in range(30)] + [{"characters": ["龙套丙"]}],
    }, body="第三章正文"))

    # 第 2 章审核快照（flavor_score=12）
    review_dir = root / ".harness" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "2.json").write_text(json.dumps(
        {"pass_review": True, "issues": [], "revise_count": 0, "deaified": False,
         "flavor_score": 12, "flavor_items": [], "at": "2026-11-18T00:00:00+00:00"},
        ensure_ascii=False), encoding="utf-8")

    # 现状：主角甲 + 世界条目（元组键 (kind,key) 由视图投影成 JSON 字符串键）
    (root / "memory" / "state.md").write_text(
        "# 现状快照\n\n## 角色现状\n\n"
        "- 主角甲｜status: alive｜realm: 青峰山｜updated: 第1章｜last_seen: 第1章\n\n"
        "## 世界现状\n\n"
        "- [place] 青峰山｜山门：主峰（importance 3；updated 第1章）\n",
        encoding="utf-8")

    # 卷级 digest
    digest_path = root / "memory" / "digests" / "vol-1.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("# 第1卷 digest\n", encoding="utf-8")

    # 章记忆：角色经历为空 → 「角色经历」∈ empty_dimensions
    MemoryStore(root).write_chapter_memory(1, "第一章", {
        "events": [{"title": "上山", "summary": "主角上山", "location": "山门",
                    "time_marker": "清晨", "characters": ["主角甲"]}],
        "character_events": [],
    })

    # 2 条 issue：0001 audit pending（带 quote/chapter），0002 canon resolved
    issues = IssuesStore(root)
    issues.add("audit", "引用失实", "详情一", quote="原文摘句", chapter=2)
    second = issues.add("canon", "设定冲突", "详情二")
    issues.set_status(second.id, "resolved")

    return pid


def test_outline_groups_plots_by_volume(client, fx):
    resp = client.get(f"/api/project/{fx}/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["planned_volume_count"] == 3
    assert [a["vol"] for a in body["arcs"]] == [1, 2]
    assert [v["vol"] for v in body["volumes"]] == [1, 2]
    assert [p["seq"] for p in body["volumes"][0]["plots"]] == [1, 2]
    assert body["volumes"][0]["plots"][0]["title"] == "开篇"
    assert [p["seq"] for p in body["volumes"][1]["plots"]] == [3]


def test_chapters_blocked_by_is_list_never_null(client, fx):
    resp = client.get(f"/api/project/{fx}/chapters")
    assert resp.status_code == 200
    by_seq = {c["seq"]: c for c in resp.json()["chapters"]}
    assert sorted(by_seq) == [1, 2, 3]
    # 第 1 章 units 里的无卡主要角色「配角乙」
    assert by_seq[1]["blocked_by"] == ["配角乙"]
    # Q12：无拦章也是 []，绝不 null
    assert by_seq[2]["blocked_by"] == []
    assert by_seq[3]["blocked_by"] == []
    # 章字段
    assert by_seq[1]["title"] == "第一章" and by_seq[1]["status"] == "done"
    assert by_seq[1]["plot_id"] == 1 and by_seq[1]["word_count"] == 2000
    # 审核快照 flavor_score：第 2 章=12，第 3 章无快照=None
    assert by_seq[2]["flavor_score"] == 12
    assert by_seq[3]["flavor_score"] is None


def test_chapter_detail_404(client, fx):
    assert client.get(f"/api/project/{fx}/chapters/99").status_code == 404
    resp = client.get(f"/api/project/{fx}/chapters/2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seq"] == 2
    assert body["body"] == "第二章正文"
    assert body["meta"]["title"] == "第二章"
    assert body["path"].endswith("0002-第二章.md")
    assert body["review"]["flavor_score"] == 12
    # 无快照的章 review=None
    assert client.get(f"/api/project/{fx}/chapters/3").json()["review"] is None


def test_characters_three_sections(client, fx):
    resp = client.get(f"/api/project/{fx}/characters")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["name"] for m in body["protagonists"]] == ["主角甲"]
    assert body["protagonists"][0]["card"]["name"] == "主角甲"
    assert [m["name"] for m in body["candidates"]] == ["配角乙"]
    assert body["candidates"][0]["card"] is None
    assert body["candidates"][0]["beats"] == 2
    assert [m["name"] for m in body["walk_ons"]] == ["龙套丙"]
    # 龙套是裸 member：无 card 键
    assert "card" not in body["walk_ons"][0]
    assert body["counts"] == {"carded": 1, "pending": 1, "walk_on": 1}


def test_memory_empty_dimensions_honest(client, fx, settings):
    resp = client.get(f"/api/project/{fx}/memory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["characters"]["主角甲"]["realm"] == "青峰山"
    # world 的 (kind, key) 元组键投影为 "kind|key" 字符串键（fastapi 编码不了元组键）
    world = body["state"]["world"]
    assert world["place|青峰山"]["value"] == "主峰"
    assert body["state_text"].startswith("# 现状快照")
    assert body["digests"] == [{"vol": 1,
                                "path": str(settings.library_root / fx / "memory" / "digests" / "vol-1.md")}]
    assert body["chapter_memories"] == [1]

    resp = client.get(f"/api/project/{fx}/memory/1")
    assert resp.status_code == 200
    cm = resp.json()
    assert cm["seq"] == 1 and cm["title"] == "第一章"
    assert cm["events"][0]["title"] == "上山"
    # 无角色卡/无经历 → 诚实文案：「角色经历」∈ empty_dimensions
    assert "角色经历" in cm["empty_dimensions"]
    # 不存在的章记忆 → 404
    assert client.get(f"/api/project/{fx}/memory/99").status_code == 404


def test_issues_filter(client, fx):
    resp = client.get(f"/api/project/{fx}/issues")
    assert resp.status_code == 200
    all_issues = resp.json()
    assert len(all_issues) == 2
    # Issue 逐字段（含 quote/chapter）
    first = all_issues[0]
    assert first["id"] == "0001" and first["source"] == "audit"
    assert first["quote"] == "原文摘句" and first["chapter"] == 2
    assert first["status"] == "pending"

    pending = client.get(f"/api/project/{fx}/issues?status=pending").json()
    assert [i["id"] for i in pending] == ["0001"]
    resolved = client.get(f"/api/project/{fx}/issues?status=resolved").json()
    assert [i["id"] for i in resolved] == ["0002"]

    by_source = client.get(f"/api/project/{fx}/issues?source=canon").json()
    assert [i["id"] for i in by_source] == ["0002"]
    audit_pending = client.get(f"/api/project/{fx}/issues?source=audit&status=pending").json()
    assert [i["id"] for i in audit_pending] == ["0001"]
