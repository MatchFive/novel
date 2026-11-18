import json

from harness.run.issues import IssuesStore


def test_add_then_list_pending(tmp_path):
    store = IssuesStore(tmp_path)
    issue = store.add(source="recon", title="人称漂移", detail="第三段切第二人称", quote="你看着窗外……")
    assert issue.id == "0001"
    assert issue.source == "recon" and issue.title == "人称漂移"
    assert issue.quote == "你看着窗外……"
    assert issue.status == "pending"
    assert issue.created_at
    assert (tmp_path / ".harness" / "issues" / "0001.json").exists()
    assert store.list() == [issue]


def test_set_status_resolved_empties_pending(tmp_path):
    store = IssuesStore(tmp_path)
    issue = store.add(source="canon", title="年代矛盾", detail="卷一写到 1998，卷二 1987")
    resolved = store.set_status(issue.id, "resolved")
    assert resolved.status == "resolved"
    assert store.list() == []
    assert store.list(status="resolved") == [resolved]


def test_two_adds_ids_increment(tmp_path):
    store = IssuesStore(tmp_path)
    a = store.add(source="x", title="a", detail="d")
    b = store.add(source="x", title="b", detail="d")
    assert a.id == "0001" and b.id == "0002"
    assert [i.id for i in store.list()] == ["0001", "0002"]


def test_next_pending_returns_oldest_or_none(tmp_path):
    store = IssuesStore(tmp_path)
    assert store.next_pending() is None
    a = store.add(source="x", title="a", detail="d")
    store.add(source="x", title="b", detail="d")
    assert store.next_pending() == a
    store.set_status("0001", "ignored")
    assert store.next_pending() == store.list()[0]


def test_list_status_filter_and_invalid_id(tmp_path):
    store = IssuesStore(tmp_path)
    store.add(source="x", title="a", detail="d")
    assert store.list(status=None) == store.list()
    try:
        store.set_status("9999", "resolved")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown id")


def test_issue_chapter_field_roundtrip(tmp_path):
    """Issue.chapter：add(chapter=7) → JSON 落盘 → 重读 list 得 7；旧格式无 chapter 读 None。"""
    store = IssuesStore(tmp_path)
    issue = store.add(source="audit", title="晚出场", detail="d", quote="q", chapter=7)
    assert issue.chapter == 7
    path = tmp_path / ".harness" / "issues" / "0001.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["chapter"] == 7
    # 重开 store（模拟新会话）读回
    again = IssuesStore(tmp_path)
    got = again.list("pending")
    assert len(got) == 1 and got[0].chapter == 7
    # 旧格式（无 chapter 键）→ None，不炸
    del payload["chapter"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    legacy = IssuesStore(tmp_path).list("pending")
    assert len(legacy) == 1 and legacy[0].chapter is None


def test_add_without_chapter_defaults_none(tmp_path):
    """签名向后兼容：不传 chapter → None，落盘 JSON 显式带 null。"""
    store = IssuesStore(tmp_path)
    issue = store.add(source="recon", title="t", detail="d")
    assert issue.chapter is None
    payload = json.loads((tmp_path / ".harness" / "issues" / "0001.json")
                         .read_text(encoding="utf-8"))
    assert payload["chapter"] is None


def test_issues_list_keeps_source(tmp_path):
    """source 原样留着：审计（audit）与验收（check）两类候选靠它分流，list 不改写。"""
    store = IssuesStore(tmp_path)
    store.add(source="audit", title="时间锚点错位", detail="d", quote="q", chapter=1)
    store.add(source="check", title="少年动机不明", detail="d", quote="q", chapter=1)
    store.add(source="check", title="全书主线偏弱", detail="d")      # 全局类：无 quote

    listed = store.list("pending")
    assert [i.source for i in listed] == ["audit", "check", "check"]
    assert listed[1].title == "少年动机不明" and listed[1].quote == "q"
    assert listed[2].quote is None and listed[2].chapter is None
    # 落盘 JSON 里 source 也原样（不被改写成别的来源）
    payload = json.loads((tmp_path / ".harness" / "issues" / "0002.json")
                         .read_text(encoding="utf-8"))
    assert payload["source"] == "check"
