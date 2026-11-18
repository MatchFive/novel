"""会话级测试基建：环境隔离（P26）。

开发者机器的真实 ``~/.harness/config.toml``（配了 default_provider）会让一批
「未配 provider」语义的测试读到真实配置而失败（典型连锁：cli_repl 的用例真起
run → 进程级 ``_RUNNER`` 后台线程残留 → 后续 p10/p16/p21 的提交全被
「已有后台任务在跑」拒掉）。Windows 上 ``HOME`` env 对 ``Path.home()`` 无效
（走 USERPROFILE），所以必须 patch ``Path.home``。本 fixture **只对历史受环境
影响的测试文件启用**，其余测试零影响。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# 历史环境性失败所在文件（2026-11-19 全量基线 16 个失败的分布）：这些文件里的
# 测试要么断言「未配 provider」的诚实退出/降级，要么经 REPL 起 run（受单 run
# 守卫的进程级全局态影响）。
_ENV_FILES = frozenset({
    "test_cli_repl.py",
    "test_config.py",
    "test_p10_acceptance.py",
    "test_p16_background_run.py",
    "test_p21_review_fixes.py",
})


@pytest.fixture(autouse=True)
def _isolate_env(request):
    """隔离真实 home 配置 + 清 REPL 后台 run 线程表（仅 _ENV_FILES 内的测试）。"""
    if Path(str(request.node.path)).name not in _ENV_FILES:
        yield
        return
    from harness import repl as repl_mod

    mp = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory() as home:
        mp.setattr(Path, "home", classmethod(lambda cls: Path(home)))
        repl_mod._RUNNER.threads.clear()          # 起手清：防上个测试的残留
        try:
            yield
        finally:
            repl_mod._RUNNER.threads.clear()      # 收手清：不毒化后续测试
            mp.undo()
