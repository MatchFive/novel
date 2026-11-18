"""Windows 保留设备名（CON/NUL/COM1…）不得当 skill 名（P5 复审遗留 3）。

口径钉死：

- ``skills._safe_segment`` 额外拒绝 Windows 保留设备名（``CON``/``PRN``/``AUX``/
  ``NUL``/``COM1-9``/``LPT1-9``，**忽略大小写与扩展名**）——``Path(base) / "CON"``
  在 Windows 上是设备名，``mkdir`` 直接抛 ``OSError``；
- ``repl._cmd_skill`` 把 ``copy_skill`` 周围 catch ``OSError`` → 如实报错、**不带栈**；
- 一个文件都不许被写到 skills 目录之外。
"""
from __future__ import annotations

import pytest

from harness.cli import main
from harness.projects import create_project
from harness.skills import import_target, resolve_import

#: Windows 保留设备名（含大小写与扩展名变体）
RESERVED = ["CON", "con", "Con", "PRN", "AUX", "NUL", "nul",
            "COM1", "com9", "LPT1", "lpt9", "con.md", "NUL.txt"]


@pytest.mark.parametrize("name", RESERVED)
def test_reserved_device_name_rejected(tmp_path, name):
    """保留设备名 → 导入被拒（一个文件都不写）。"""
    src = tmp_path / "evil.md"
    src.write_text(f"---\nname: {name}\n---\n\n正文\n", encoding="utf-8")

    with pytest.raises(ValueError):
        resolve_import(src)
    with pytest.raises(ValueError):
        import_target(tmp_path / "skills", name)
    skills_dir = tmp_path / "skills"
    assert not skills_dir.exists() or list(skills_dir.iterdir()) == []


@pytest.mark.parametrize("name", ["console", "auxiliary", "com10", "lpt0", "nullify"])
def test_non_reserved_names_still_allowed(tmp_path, name):
    """非保留名照常放行（不得连坐）。"""
    src = tmp_path / "ok.md"
    src.write_text(f"---\nname: {name}\n---\n\n正文\n", encoding="utf-8")

    got, _src_path, _is_dir = resolve_import(src)

    assert got == name


def test_skill_import_con_name_reports_cleanly(tmp_path, monkeypatch, capsys):
    """``/skill import`` 一个名为 ``CON`` 的 skill → 干净报错（非 traceback），
    且**没有任何文件**被写到 skills 目录外。"""
    info = create_project(tmp_path, "设备名书")
    src = tmp_path / "外部"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: CON\ndescription: 设备名\n---\n\n正文\n",
                                  encoding="utf-8")
    home = tmp_path / "home_skills"
    home.mkdir()
    monkeypatch.setattr("harness.skills.user_skills_dir", lambda: home)

    inputs = iter([f"/skill import {src / 'SKILL.md'}", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main([str(info.path)]) == 0

    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "CON" in out                     # 如实点名被拒的名字
    assert list(home.iterdir()) == []       # skills 目录里一个文件都没落
    assert list((info.path / ".harness" / "skills").iterdir()) == []
