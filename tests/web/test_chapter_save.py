"""Task 13：正文编辑——PUT 章节（doc.path 纪律，§4.8）。

Review Focus #1：章号取自 front-matter、与文件名前缀可能分叉（手写章常见）。
``test_put_uses_doc_path_not_glob`` 造两个「交叉错位」的手写章：

- ``chapters/0003-x.md``：front-matter ``seq: 5``（文件名前缀 3 ≠ 章号 5）
- ``chapters/0005-y.md``：front-matter ``seq: 3``（文件名前缀 5 ≠ 章号 3）

PUT seq=5 必须写进 ``0003-x.md``（read 回来的 doc.path），seq=3 的章（``0005-y.md``）
逐字节不动——按 seq 重新 glob / 重推导文件名的实现会落到 ``0005-y.md``、改错章。
"""

import pytest
from fastapi.testclient import TestClient

from harness.config import Settings
from harness.text import count_words


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
def pid(client):
    resp = client.post("/api/projects", json={"title": "正文编辑书"})
    assert resp.status_code == 200
    return resp.json()["id"]


def _write_chapter(root, name: str, text: str):
    path = root / "chapters" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_put_updates_body_keeps_meta(tmp_path, client, settings, pid):
    root = settings.library_root / pid
    path = _write_chapter(root, "0001-first.md", (
        "---\n"
        "seq: 1\n"
        "title: 开篇\n"
        "status: draft\n"
        "word_count: 10\n"
        "---\n"
        "\n"
        "旧正文第一段。\n"
        "\n"
        "旧正文第二段。\n"
    ))
    new_body = "新正文第一段。\n\n新正文第二段。"

    resp = client.put(f"/api/project/{pid}/chapters/1", json={"body": new_body})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"seq": 1, "word_count": count_words(new_body), "status": "draft"}
    # front-matter 原样保留（title/status 不换），正文段已替换
    text = path.read_text(encoding="utf-8")
    assert "title: 开篇" in text and "status: draft" in text
    assert "新正文第一段。" in text and "旧正文第一段" not in text
    # 经 store 读回：meta 不丢、body 已是新正文（写盘用 strip_edges 口径）
    from harness.store.project import ProjectStore

    doc = ProjectStore(root).read_chapter(1)
    assert doc.body == new_body
    assert doc.meta["title"] == "开篇" and doc.meta["status"] == "draft"


def test_put_uses_doc_path_not_glob(tmp_path, client, settings, pid):
    root = settings.library_root / pid
    ch5 = _write_chapter(root, "0003-x.md", (
        "---\nseq: 5\ntitle: 错位五\nstatus: draft\n---\n\n五的旧正文\n"
    ))
    ch3 = _write_chapter(root, "0005-y.md", (
        "---\nseq: 3\ntitle: 错位三\nstatus: done\n---\n\n三的旧正文\n"
    ))
    ch3_before = ch3.read_bytes()

    resp = client.put(f"/api/project/{pid}/chapters/5", json={"body": "五的新正文"})

    assert resp.status_code == 200
    assert resp.json()["seq"] == 5
    # 新正文落进 0003-x.md（meta seq=5 的那个文件），front-matter 原文逐字节保留
    text5 = ch5.read_text(encoding="utf-8")
    assert "五的新正文" in text5 and "五的旧正文" not in text5
    assert "seq: 5" in text5 and "错位五" in text5
    # seq=3 的章（文件 0005-y.md）逐字节不动；也没有新建 0005-错位五.md
    assert ch3.read_bytes() == ch3_before
    assert not (root / "chapters" / "0005-错位五.md").exists()


def test_put_missing_chapter_404(tmp_path, client, settings, pid):
    root = settings.library_root / pid
    _write_chapter(root, "0001-a.md", "---\nseq: 1\ntitle: 唯一章\n---\n\n正文\n")

    resp = client.put(f"/api/project/{pid}/chapters/99", json={"body": "新正文"})

    assert resp.status_code == 404


def test_put_bad_frontmatter_409(tmp_path, client, settings, pid):
    """front-matter 坏的章绝不跳过猜着改：失败关闭 → 409。"""
    root = settings.library_root / pid
    _write_chapter(root, "0001-a.md", "---\nseq: 1\ntitle: 好章\n---\n\n正文\n")
    _write_chapter(root, "0002-bad.md", "---\nseq: 2\n: : 坏掉的 yaml\n---\n\n正文\n")

    resp = client.put(f"/api/project/{pid}/chapters/1", json={"body": "新正文"})

    assert resp.status_code == 409
