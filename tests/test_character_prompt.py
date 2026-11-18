"""角色起草提示词契约测试（task.auto_character_create，Task 16）。

1. render：8 个变量逐字提供 → 渲染成功且 name 落入正文。
2. get：输出契约段（【输出格式】）钉死 §4.6.2 字段（profile 六键）。
3. 变量集合 == 本地契约 8 个（Task 17 渲染契约，多一个少一个都失败）。
4. 行尾守卫：模板文件必须 LF（Windows 上写文件易出 CRLF）。
"""
import re
from importlib import resources

from harness.prompts.loader import PromptLoader

EXPECTED_VARS = {
    "project_info", "world_entries", "existing_characters", "role_type",
    "role_label", "role_guide", "requirement", "name",
}

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def test_template_renders_with_all_vars():
    from harness.prompts.loader import PromptLoader
    t = PromptLoader().render("task.auto_character_create",
        project_info="p", world_entries="w", existing_characters="e",
        role_type="major", role_label="重要配角", role_guide="g", requirement="r", name="苏晚")
    assert "苏晚" in t


def test_template_has_output_contract():
    text = PromptLoader().get("task.auto_character_create")
    assert "【输出格式】" in text and "personality" in text and "speech_style" in text


def test_template_var_set_is_local_contract():
    text = PromptLoader().get("task.auto_character_create")
    assert set(_VAR_RE.findall(text)) == EXPECTED_VARS


def test_template_file_is_lf_only():
    ref = (resources.files("harness.prompts").joinpath("builtin")
           .joinpath("task.auto_character_create.md"))
    data = ref.read_bytes()
    assert b"\r\n" not in data
