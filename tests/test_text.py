from harness.text import as_list, as_text, count_words, strip_chapter_range, strip_edges


def test_strip_edges_removes_blank_lines_at_edges():
    assert strip_edges("\n\n正文\n\n\n") == "正文"

def test_as_text_joins_list_with_newline():
    assert as_text(["a", "b"]) == "a\nb"
    assert as_text("plain") == "plain"
    assert as_text(None) is None

def test_as_list_wraps_scalar():
    assert as_list("x") == ["x"]
    assert as_list(["x", "y"]) == ["x", "y"]
    assert as_list(None) == []

def test_strip_chapter_range():
    assert strip_chapter_range("夜袭（第3—5章）") == "夜袭"

def test_count_words_counts_cjk():
    assert count_words("大道朝天，各走一边。") > 0
