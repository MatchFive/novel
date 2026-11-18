"""tests for harness.config — 两级 TOML 配置加载（全局 + 项目级）。"""

from pathlib import Path

from harness.config import load_settings


def test_defaults_when_no_files():
    # load_settings 调用期求值 Path.home()：home 隔离由 tests/conftest.py 的
    # _isolate_env 提供（patch Path.home——HOME env 在 Windows 对 Path.home() 无效）
    s = load_settings()
    assert s.library_root == Path.home() / "novels"
    assert s.web_verify is True          # 拍板⑩
    assert s.providers == {}


def test_missing_provider_field_raises(tmp_path):
    g = tmp_path / "config.toml"
    g.write_text('[providers.deepseek]\nbase_url = "https://api.deepseek.com"\n'
                 'api_key = "k"\nlite = "deepseek-chat"\nstandard = "deepseek-chat"\n',
                 encoding="utf-8")  # 缺 pro
    try:
        load_settings(global_path=g)
    except ValueError as exc:
        assert "pro" in str(exc) and "deepseek" in str(exc)
    else:
        raise AssertionError("缺字段的 provider 表应抛 ValueError")


def test_global_file_parsed(tmp_path):
    g = tmp_path / "config.toml"
    g.write_text('library_root = "D:/novels"\nweb_verify = false\n'
                 '[providers.deepseek]\nbase_url = "https://api.deepseek.com"\n'
                 'api_key = "k"\nlite = "deepseek-chat"\nstandard = "deepseek-chat"\npro = "deepseek-reasoner"\n'
                 'default_provider = "deepseek"\n', encoding="utf-8")
    s = load_settings(global_path=g)
    assert s.library_root == Path("D:/novels")
    assert s.web_verify is False
    assert s.providers["deepseek"].pro == "deepseek-reasoner"


def test_project_override_wins(tmp_path):
    g = tmp_path / "g.toml"; g.write_text('default_tier = "standard"\n', encoding="utf-8")
    p = tmp_path / "p.toml"; p.write_text('default_tier = "pro"\n', encoding="utf-8")
    assert load_settings(global_path=g, project_path=p).default_tier == "pro"
