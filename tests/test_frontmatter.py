import pytest

from harness.store.frontmatter import FileFormatError, render_document, split_document, split_file


def test_round_trip_preserves_body_verbatim(tmp_path):
    meta = {"seq": 29, "title": "夜袭", "status": "done",
            "beats": [{"pov": "陆昭", "location": "城头", "event": "袭杀"}]}
    body = "第一段。\n\n第二段。"
    text = render_document(meta, body)
    meta2, body2 = split_document(text)
    assert meta2 == meta
    assert body2 == body

def test_broken_yaml_backups_and_raises(tmp_path):
    f = tmp_path / "0029-夜袭.md"
    f.write_text("---\nseq: [unclosed\n---\n正文\n", encoding="utf-8")
    with pytest.raises(FileFormatError) as ei:
        split_file(f)
    assert ei.value.path == f
    assert (tmp_path / "0029-夜袭.md.bak").read_text(encoding="utf-8") == f.read_text(encoding="utf-8")

def test_body_starts_after_first_blank_line():
    _, body = split_document("---\nseq: 1\n---\n\n正文起点\n\n---\n不是 front-matter 的横线\n")
    assert body.startswith("正文起点")

def test_render_strips_body_edges():
    out = render_document({"seq": 1}, "\n\n正文\n\n")
    assert out.rstrip().endswith("正文")
