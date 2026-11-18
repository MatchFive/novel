from harness.projects import create_project
from harness.store.project import ChapterDoc, ProjectStore


def test_p0_acceptance(tmp_path):
    """设计 §3 P0 验收：建项目 → 写章 → 带外手改 md → 读回一致。"""
    info = create_project(tmp_path, "验收书")
    store = ProjectStore(info.path)
    store.write_chapter(ChapterDoc(seq=1, meta={"seq": 1, "title": "首章", "status": "done"}, body="正文"))
    # 手改 md
    f = info.path / "chapters" / "0001-首章.md"
    f.write_text(f.read_text(encoding="utf-8").replace("正文", "手改后正文"), encoding="utf-8")
    assert store.read_chapter(1).body == "手改后正文"
