"""模型配置 GUI 化测试：save/load 往返 + 任务级模型指定（不调用 LLM）。运行：python tests/test_model_config.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import (  # noqa: E402
    AppConfig, ModelSpec, ProviderConfig, RoutingConfig, load_config, save_config,
)
from app.llm.router import TaskSpec, resolve_model  # noqa: E402


def main() -> int:
    try:
        app = QApplication(sys.argv)
        tmp = Path(tempfile.mkdtemp())
        cfg_path = tmp / "config.toml"

        cfg = AppConfig(
            data_dir=tmp / "data",
            providers={
                "deepseek": ProviderConfig(
                    base_url="https://api.deepseek.com/v1", api_key="sk-x",
                    models=ModelSpec(lite="deepseek-chat", standard="deepseek-chat",
                                     pro="deepseek-reasoner")),
                "local": ProviderConfig(
                    base_url="http://127.0.0.1:8000/v1", api_key="",
                    models=ModelSpec(lite="qwen3-14b", standard="qwen3-14b", pro="qwen3-14b")),
            },
            routing=RoutingConfig(
                default_provider="deepseek", default_tier="standard", auto_downgrade=True,
                task_models={"task.draft_continue": {"provider": "local", "model": "qwen3-14b"}},
            ),
            gen_config={"target_words": 3000, "pace": "fast", "temperature": None},
        )

        # ---- save → load 往返 ----
        save_config(cfg, cfg_path)
        cfg2 = load_config(cfg_path)
        assert set(cfg2.providers) == {"deepseek", "local"}
        assert cfg2.providers["local"].base_url == "http://127.0.0.1:8000/v1"
        assert cfg2.providers["deepseek"].models.pro == "deepseek-reasoner"
        assert cfg2.routing.task_models["task.draft_continue"]["provider"] == "local"
        assert cfg2.routing.task_models["task.draft_continue"]["model"] == "qwen3-14b"
        assert cfg2.gen_config["pace"] == "fast" and cfg2.gen_config["target_words"] == 3000
        print("save/load roundtrip OK")

        # ---- 任务级指定：draft_continue 走本地 qwen3-14b ----
        r = resolve_model(cfg2, TaskSpec(module="draft", prompt_key="task.draft_continue"))
        assert r.provider == "local" and r.model == "qwen3-14b", r
        print("task override OK:", r.provider, r.model)

        # ---- 未指定的任务仍走默认路由 ----
        r2 = resolve_model(cfg2, TaskSpec(module="chat", prompt_key="chat"))
        assert r2.provider == "deepseek" and r2.model == "deepseek-chat", r2
        print("default routing intact OK")

        # ---- 指定 provider 无效时回退默认 ----
        cfg3 = AppConfig(
            data_dir=tmp / "data", providers=cfg.providers,
            routing=RoutingConfig(default_provider="deepseek",
                                  task_models={"task.chat_x": {"provider": "ghost", "model": "m"}}),
        )
        r3 = resolve_model(cfg3, TaskSpec(module="chat", prompt_key="task.chat_x"))
        assert r3.provider == "deepseek", r3
        print("invalid provider fallback OK")

        # ---- 路由表前缀兼容：task.* 键能命中表（修复前路由表从未命中） ----
        r4 = resolve_model(cfg2, TaskSpec(module="outline", prompt_key="task.outline_generate"))
        assert r4.tier == "pro" and r4.model == "deepseek-reasoner", r4
        r5 = resolve_model(cfg2, TaskSpec(module="handbook", prompt_key="task.handbook_extract"))
        assert r5.max_tokens == 8192, r5
        r6 = resolve_model(cfg2, TaskSpec(module="command", prompt_key="task.context_reconcile"))
        assert r6.tier == "lite" and r6.model == "deepseek-chat", r6
        print("routing table prefix fix OK (pro tier / max_tokens / lite)")

        # ---- 对话框构造（不 exec） ----
        from app.ui.dialogs.model_settings_dialog import ModelSettingsDialog
        dlg = ModelSettingsDialog(cfg2)
        assert dlg.provider_list.count() == 2
        assert dlg.task_table.rowCount() == 1
        assert dlg.task_table.cellWidget(0, 1).currentText() == "local"
        assert dlg.task_table.cellWidget(0, 2).text() == "qwen3-14b"
        print("settings dialog OK")

        print("ALL MODEL CONFIG TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
