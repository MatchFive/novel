from harness.projects import create_project, list_projects, open_project, slugify


def test_slugify_keeps_cjk():
    assert slugify("大道朝天") == "大道朝天"
    assert slugify("My Novel! 第二部") == "my-novel-第二部"


def test_create_project_scaffold(tmp_path):
    p = create_project(tmp_path, "大道朝天", {"genre": "仙侠", "chapter_words": 3000})
    for rel in ["README.md", "world.md", "characters", "outline/arcs.md",
                "outline/plots", "chapters", "memory/state.md", ".harness/skills", ".harness/prompts"]:
        assert (p.path / rel).exists(), rel
    readme = (p.path / "README.md").read_text(encoding="utf-8")
    assert "题材：仙侠" in readme and "每章字数：3000" in readme


def test_display_name_differs_from_slug(tmp_path):
    p = create_project(tmp_path, "My Novel! 第二部")
    assert p.path.name == "my-novel-第二部"
    assert p.title == "My Novel! 第二部"
    assert open_project(p.path).title == "My Novel! 第二部"


def test_slug_collision_suffix(tmp_path):
    create_project(tmp_path, "大道朝天")
    p2 = create_project(tmp_path, "大道朝天")
    assert p2.path.name == "大道朝天-2"


def test_list_projects_only_readme_dirs(tmp_path):
    create_project(tmp_path, "甲")
    (tmp_path / "随手放的文件夹").mkdir()
    names = [p.title for p in list_projects(tmp_path)]
    assert names == ["甲"]


def test_library_root_auto_created(tmp_path):
    root = tmp_path / "deep" / "novels"
    list_projects(root)  # 不抛错
    assert root.is_dir()
