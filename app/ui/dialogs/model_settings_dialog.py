"""模型与路由设置对话框（图形化配置，替代手改 config.toml）：

- 厂商管理：base_url / api_key / 三档模型（本地部署的 OpenAI 兼容服务也算一个厂商）
- 默认路由：默认厂商 / 默认档位 / 自动降级
- 任务级指定：某个任务（如「正文续写」）固定使用某厂商的某模型——
  例如正文生成走本地 qwen3-14b，其余任务走云端
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.config import AppConfig, EmbeddingConfig, ModelSpec, ProviderConfig, RoutingConfig, save_config
from app.core.constants import ALL_TIERS, PROMPTS_JSON


def _task_choices() -> list[tuple[str, str]]:
    """可选任务列表：(prompt_key, 显示名)。来自内置提示词模板 + 对话。"""
    choices: list[tuple[str, str]] = [("chat", "AI 助手对话")]
    try:
        with PROMPTS_JSON.open("r", encoding="utf-8") as f:
            templates = json.load(f)
        for key, item in templates.items():
            if key.startswith("task."):
                choices.append((key, item.get("name") or key))
    except Exception:
        pass
    return choices


class ModelSettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模型与路由设置")
        self.setMinimumSize(760, 560)
        self.cfg = cfg
        self.new_cfg: AppConfig | None = None
        # 编辑中的厂商数据：{name: {base_url, api_key, lite, standard, pro}}
        self._providers: dict[str, dict] = {
            name: {
                "base_url": p.base_url, "api_key": p.api_key,
                "lite": p.models.lite, "standard": p.models.standard, "pro": p.models.pro,
            }
            for name, p in cfg.providers.items()
        }
        self._loading = False

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "厂商 = 一个 OpenAI 兼容接口（云端 API 或本地部署的 vLLM/Ollama/LMDeploy 等）；"
            "任务级指定优先级最高，可让「正文续写」等任务固定走本地模型。"))

        splitter = QSplitter()
        # ---- 左：厂商列表 ----
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("厂商列表"))
        self.provider_list = QListWidget()
        self.provider_list.currentItemChanged.connect(self._on_provider_selected)
        ll.addWidget(self.provider_list)
        prow = QHBoxLayout()
        self.btn_add_p = QPushButton("＋ 新增厂商")
        self.btn_add_p.clicked.connect(self._add_provider)
        self.btn_del_p = QPushButton("删除厂商")
        self.btn_del_p.clicked.connect(self._del_provider)
        prow.addWidget(self.btn_add_p)
        prow.addWidget(self.btn_del_p)
        ll.addLayout(prow)
        splitter.addWidget(left)

        # ---- 右：厂商编辑表单 ----
        right = QWidget()
        rl = QVBoxLayout(right)
        form = QFormLayout()
        self.f_name = QLineEdit()
        self.f_name.setReadOnly(True)
        self.f_base = QLineEdit()
        self.f_base.setPlaceholderText("如 https://api.deepseek.com/v1 或 http://127.0.0.1:8000/v1")
        self.f_key = QLineEdit()
        self.f_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_key.setPlaceholderText("本地服务可留空")
        self.f_lite = QLineEdit()
        self.f_std = QLineEdit()
        self.f_pro = QLineEdit()
        form.addRow("名称", self.f_name)
        form.addRow("Base URL", self.f_base)
        form.addRow("API Key", self.f_key)
        form.addRow("Lite 档模型", self.f_lite)
        form.addRow("Standard 档模型", self.f_std)
        form.addRow("Pro 档模型", self.f_pro)
        rl.addLayout(form)
        rl.addWidget(QLabel("（切换厂商时自动暂存表单内容）"))
        rl.addStretch(1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        lay.addWidget(splitter, 1)

        # ---- 默认路由 ----
        rrow = QHBoxLayout()
        rrow.addWidget(QLabel("默认厂商："))
        self.default_provider = QComboBox()
        rrow.addWidget(self.default_provider)
        rrow.addWidget(QLabel("默认档位："))
        self.default_tier = QComboBox()
        for t in ALL_TIERS:
            self.default_tier.addItem(t, t)
        rrow.addWidget(self.default_tier)
        self.auto_downgrade = QCheckBox("Pro 失败自动降级 Standard")
        rrow.addWidget(self.auto_downgrade)
        rrow.addStretch(1)
        lay.addLayout(rrow)

        # ---- 意图识别路由（显眼位置，独立配置） ----
        intent_box = QHBoxLayout()
        intent_box.addWidget(QLabel("🧭 对话意图识别："))
        self.intent_provider = QComboBox()
        self.intent_provider.addItem("跟随默认厂商", "")
        for name in self._providers:
            self.intent_provider.addItem(name, name)
        intent_box.addWidget(self.intent_provider)
        intent_box.addWidget(QLabel("模型："))
        self.intent_model = QLineEdit()
        self.intent_model.setPlaceholderText("如 qwen3:4b；留空=用该厂商 Lite 档模型")
        intent_box.addWidget(self.intent_model, 1)
        self.intent_thinking = QCheckBox("思考")
        self.intent_thinking.setToolTip("开启后调用该模型时启用思考/推理模式（DeepSeek thinking / Qwen3 enable_thinking）")
        intent_box.addWidget(self.intent_thinking)
        intent_box.addWidget(QLabel("（识别失败自动回退关键词匹配）"))
        intent_box.addStretch(1)
        lay.addLayout(intent_box)

        # ---- 语义检索（嵌入服务，上下文组装用） ----
        emb_box = QHBoxLayout()
        emb_box.addWidget(QLabel("🔎 语义检索嵌入："))
        self.emb_provider = QComboBox()
        self.emb_provider.addItem("不启用（用关键词/FTS）", "__disabled__")
        self.emb_provider.addItem("内置 fastembed（进程内，无需另开服务）", "__fastembed__")
        for name in self._providers:
            self.emb_provider.addItem(name, name)
        emb_box.addWidget(self.emb_provider)
        emb_box.addWidget(QLabel("模型："))
        self.emb_model = QLineEdit()
        self.emb_model.setPlaceholderText("留空=all-MiniLM-L6-v2；中文建议 bge-small-zh-v1.5")
        emb_box.addWidget(self.emb_model, 1)
        emb_box.addWidget(QLabel("（按语义给 AI 助手挑相关设定/角色；内置后端首次运行会下载约 90MB 模型）"))
        emb_box.addStretch(1)
        lay.addLayout(emb_box)

        # ---- 任务级指定 ----
        lay.addWidget(QLabel("任务级指定（该任务固定使用某厂商的某模型；模型留空 = 用该厂商当前档位模型；"
                             "「思考」= 该任务调用时启用思考/推理模式，适合拆章/正文等高价值任务）"))
        self.task_table = QTableWidget(0, 4)
        self.task_table.setHorizontalHeaderLabels(["任务", "厂商", "模型（可留空）", "思考"])
        self.task_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.task_table, 1)
        trow = QHBoxLayout()
        btn_add_t = QPushButton("＋ 添加指定")
        btn_add_t.clicked.connect(lambda: self._add_task_row("", "", "", False))
        btn_del_t = QPushButton("删除选中行")
        btn_del_t.clicked.connect(self._del_task_row)
        trow.addWidget(btn_add_t)
        trow.addWidget(btn_del_t)
        trow.addStretch(1)
        lay.addLayout(trow)

        # ---- 保存 ----
        brow = QHBoxLayout()
        brow.addStretch(1)
        btn_save = QPushButton("保存并生效")
        btn_save.setObjectName("ai_button")
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        brow.addWidget(btn_save)
        brow.addWidget(btn_cancel)
        lay.addLayout(brow)

        self._reload_provider_list()
        # 填充路由与任务表
        idx = self.default_provider.findText(cfg.routing.default_provider)
        if idx >= 0:
            self.default_provider.setCurrentIndex(idx)
        tidx = self.default_tier.findData(cfg.routing.default_tier)
        if tidx >= 0:
            self.default_tier.setCurrentIndex(tidx)
        self.auto_downgrade.setChecked(bool(cfg.routing.auto_downgrade))
        # 意图识别路由回显
        intent_spec = (cfg.routing.task_models or {}).get("task.intent_classify") or {}
        iidx = self.intent_provider.findData(intent_spec.get("provider", ""))
        self.intent_provider.setCurrentIndex(iidx if iidx >= 0 else 0)
        self.intent_model.setText(intent_spec.get("model", ""))
        self.intent_thinking.setChecked(bool(intent_spec.get("thinking")))
        # 语义检索嵌入回显（空配置 = 自动启用内置 fastembed，如实显示）
        emb_data = cfg.embedding.provider or "__fastembed__"
        eidx = self.emb_provider.findData(emb_data)
        self.emb_provider.setCurrentIndex(eidx if eidx >= 0 else 1)
        self.emb_model.setText(cfg.embedding.model or "")
        for key, spec in (cfg.routing.task_models or {}).items():
            if key == "task.intent_classify":
                continue  # 意图识别有专属配置区，不在通用表格里重复显示
            self._add_task_row(key, spec.get("provider", ""), spec.get("model", ""),
                               bool(spec.get("thinking")))

    # ---------------- 厂商表单 ----------------
    def _reload_provider_list(self, select: str | None = None) -> None:
        self._loading = True
        self.provider_list.clear()
        for name in self._providers:
            self.provider_list.addItem(name)
        self._loading = False
        self._refresh_default_combo()
        if self._providers:
            row = list(self._providers).index(select) if select in self._providers else 0
            self.provider_list.setCurrentRow(row)

    def _refresh_default_combo(self) -> None:
        cur = self.default_provider.currentText()
        self.default_provider.clear()
        for name in self._providers:
            self.default_provider.addItem(name)
        idx = self.default_provider.findText(cur)
        if idx >= 0:
            self.default_provider.setCurrentIndex(idx)
        # 意图识别厂商下拉联动刷新（保留已选项）
        intent_cur = self.intent_provider.currentData() or ""
        self.intent_provider.clear()
        self.intent_provider.addItem("跟随默认厂商", "")
        for name in self._providers:
            self.intent_provider.addItem(name, name)
        iidx = self.intent_provider.findData(intent_cur)
        self.intent_provider.setCurrentIndex(iidx if iidx >= 0 else 0)
        # 语义检索嵌入厂商下拉联动刷新（保留已选项）
        emb_cur = self.emb_provider.currentData() or ""
        self.emb_provider.clear()
        self.emb_provider.addItem("不启用（用关键词/FTS）", "")
        self.emb_provider.addItem("内置 fastembed（进程内，无需另开服务）", "__fastembed__")
        for name in self._providers:
            self.emb_provider.addItem(name, name)
        eidx = self.emb_provider.findData(emb_cur)
        self.emb_provider.setCurrentIndex(eidx if eidx >= 0 else 0)

    def _current_provider_name(self) -> str | None:
        item = self.provider_list.currentItem()
        return item.text() if item else None

    def _stash_form(self, name: str | None) -> None:
        """把表单内容写回 _providers。"""
        if not name or name not in self._providers:
            return
        self._providers[name] = {
            "base_url": self.f_base.text().strip(),
            "api_key": self.f_key.text().strip(),
            "lite": self.f_lite.text().strip(),
            "standard": self.f_std.text().strip(),
            "pro": self.f_pro.text().strip(),
        }

    def _on_provider_selected(self, current, previous) -> None:
        if self._loading:
            return
        if previous is not None:
            self._stash_form(previous.text())
        name = self._current_provider_name()
        d = self._providers.get(name or "", {})
        self.f_name.setText(name or "")
        self.f_base.setText(d.get("base_url", ""))
        self.f_key.setText(d.get("api_key", ""))
        self.f_lite.setText(d.get("lite", ""))
        self.f_std.setText(d.get("standard", ""))
        self.f_pro.setText(d.get("pro", ""))

    def _add_provider(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新增厂商", "厂商名（如 local、openai）：")
        if not ok:
            return
        name = name.strip()
        if not name or name in self._providers:
            QMessageBox.warning(self, "新增厂商", "名称为空或已存在。")
            return
        self._stash_form(self._current_provider_name())
        self._providers[name] = {"base_url": "", "api_key": "",
                                 "lite": "", "standard": "", "pro": ""}
        self._reload_provider_list(select=name)

    def _del_provider(self) -> None:
        name = self._current_provider_name()
        if not name:
            return
        if len(self._providers) <= 1:
            QMessageBox.warning(self, "删除厂商", "至少保留一个厂商。")
            return
        del self._providers[name]
        self._reload_provider_list()

    # ---------------- 任务级指定 ----------------
    def _add_task_row(self, key: str, provider: str, model: str, thinking: bool = False) -> None:
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        task_combo = QComboBox()
        task_combo.setEditable(False)
        for k, label in _task_choices():
            task_combo.addItem(f"{label}（{k}）", k)
        idx = task_combo.findData(key)
        task_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.task_table.setCellWidget(row, 0, task_combo)

        prov_combo = QComboBox()
        for name in self._providers:
            prov_combo.addItem(name)
        pidx = prov_combo.findText(provider)
        prov_combo.setCurrentIndex(pidx if pidx >= 0 else 0)
        self.task_table.setCellWidget(row, 1, prov_combo)

        model_edit = QLineEdit(model)
        model_edit.setPlaceholderText("如 qwen3-14b；留空=跟随档位")
        self.task_table.setCellWidget(row, 2, model_edit)

        thinking_box = QCheckBox()
        thinking_box.setChecked(thinking)
        thinking_box.setToolTip("思考/推理模式：DeepSeek thinking.type=enabled；Qwen3/本地 enable_thinking")
        self.task_table.setCellWidget(row, 3, thinking_box)

    def _del_task_row(self) -> None:
        row = self.task_table.currentRow()
        if row >= 0:
            self.task_table.removeRow(row)

    # ---------------- 保存 ----------------
    def _save(self) -> None:
        self._stash_form(self._current_provider_name())
        # 校验
        for name, d in self._providers.items():
            if not d["base_url"]:
                QMessageBox.warning(self, "保存", f"厂商「{name}」缺少 Base URL。")
                return
            if not d["standard"]:
                QMessageBox.warning(self, "保存", f"厂商「{name}」缺少 Standard 档模型。")
                return
        providers = {
            name: ProviderConfig(
                base_url=d["base_url"], api_key=d["api_key"],
                models=ModelSpec(lite=d["lite"] or d["standard"],
                                 standard=d["standard"],
                                 pro=d["pro"] or d["standard"]),
            )
            for name, d in self._providers.items()
        }
        default_provider = self.default_provider.currentText()
        if default_provider not in providers:
            default_provider = next(iter(providers))

        task_models: dict[str, dict] = {}
        for row in range(self.task_table.rowCount()):
            key = self.task_table.cellWidget(row, 0).currentData()
            prov = self.task_table.cellWidget(row, 1).currentText()
            model = self.task_table.cellWidget(row, 2).text().strip()
            thinking = self.task_table.cellWidget(row, 3).isChecked()
            if prov not in providers:
                continue
            task_models[key] = {"provider": prov, "model": model, "thinking": thinking}
        # 意图识别专用路由（显眼配置区；选「跟随默认厂商」则不写 task_models，走默认 lite 档）
        intent_prov = self.intent_provider.currentData() or ""
        if intent_prov and intent_prov in providers:
            task_models["task.intent_classify"] = {
                "provider": intent_prov, "model": self.intent_model.text().strip(),
                "thinking": self.intent_thinking.isChecked()}
        else:
            task_models.pop("task.intent_classify", None)

        routing = RoutingConfig(
            default_provider=default_provider,
            default_tier=self.default_tier.currentData() or "standard",
            auto_downgrade=self.auto_downgrade.isChecked(),
            task_models=task_models,
        )
        # 语义检索嵌入：HTTP 后端需要模型名；内置 fastembed 留空=默认模型；不启用无需模型
        emb_prov = self.emb_provider.currentData() or ""
        emb_model = self.emb_model.text().strip()
        if emb_prov and emb_prov not in ("__fastembed__", "__disabled__") and not emb_model:
            QMessageBox.warning(self, "保存", "语义检索嵌入已选厂商但未填模型——请填写嵌入模型名（如 all-MiniLM-L6-v2），或把厂商改回「不启用」。")
            return
        new_cfg = AppConfig(
            data_dir=self.cfg.data_dir,
            providers=providers,
            routing=routing,
            embedding=EmbeddingConfig(provider=emb_prov, model=emb_model),
            theme=self.cfg.theme,
            gen_config=dict(self.cfg.gen_config),
        )
        try:
            save_config(new_cfg)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.new_cfg = new_cfg
        self.accept()
