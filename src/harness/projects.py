"""项目脚手架与库扫描（目录契约 §2.2）。

README.md 是 story_settings 的唯一来源：
题材 / 基调 / 结局 / 每章字数 / 目标总章数 / 预计卷数，缺省留空。
显示名存 README，文件夹名 = slug，二者可不同。
"""

import re
from dataclasses import dataclass
from pathlib import Path

README_FIELDS = [
    ("genre", "题材"),
    ("tone", "基调"),
    ("ending", "结局"),
    ("chapter_words", "每章字数"),
    ("target_chapters", "目标总章数"),
    ("planned_volumes", "预计卷数"),
]

README_NAME = "README.md"


@dataclass
class ProjectInfo:
    path: Path
    title: str


def slugify(title: str) -> str:
    """小写、空白→-、删除非 [CJK/字母数字下划线/连字符]、折叠并去除首尾 -。

    中文不转拼音。
    """
    s = title.lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w一-鿿-]", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _render_readme(title: str, story: dict | None) -> str:
    story = story or {}
    lines = [f"# {title}", ""]
    for key, label in README_FIELDS:
        lines.append(f"{label}：{story.get(key, '')}")
    return "\n".join(lines) + "\n"


def _read_title(path: Path) -> str:
    readme = path / README_NAME
    for line in readme.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return path.name


def _next_available_slug(library_root: Path, base: str) -> str:
    candidate = base
    n = 2
    while (library_root / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def create_project(library_root: Path, title: str, story: dict | None = None) -> ProjectInfo:
    library_root = Path(library_root)
    library_root.mkdir(parents=True, exist_ok=True)
    slug = _next_available_slug(library_root, slugify(title))
    path = library_root / slug

    (path / "characters").mkdir(parents=True)
    (path / "outline" / "plots").mkdir(parents=True)
    (path / "chapters").mkdir(parents=True)
    (path / "memory").mkdir(parents=True)
    (path / ".harness" / "skills").mkdir(parents=True)
    (path / ".harness" / "prompts").mkdir(parents=True)

    (path / README_NAME).write_text(_render_readme(title, story), encoding="utf-8")
    (path / "world.md").write_text("# 世界观\n", encoding="utf-8")
    (path / "outline" / "arcs.md").write_text("# 卷骨架\n", encoding="utf-8")
    (path / "memory" / "state.md").write_text("# 现状快照\n\n## 角色现状\n\n## 世界现状\n", encoding="utf-8")

    # git 自动快照（拍板⑧，P35）：建项目即 git init + .gitignore（.harness/ 不进库）
    from harness.snapshot import ensure_repo
    ensure_repo(path)

    return ProjectInfo(path=path, title=title)


def list_projects(library_root: Path) -> list[ProjectInfo]:
    library_root = Path(library_root)
    library_root.mkdir(parents=True, exist_ok=True)
    projects = []
    for child in sorted(library_root.iterdir()):
        if child.is_dir() and (child / README_NAME).exists():
            projects.append(ProjectInfo(path=child, title=_read_title(child)))
    return projects


def open_project(path: Path) -> ProjectInfo:
    path = Path(path)
    readme = path / README_NAME
    if not readme.exists():
        raise FileNotFoundError(f"项目缺少 {readme}")
    return ProjectInfo(path=path, title=_read_title(path))
