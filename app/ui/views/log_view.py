"""日志查看器：LLM 调用历史（按天文件，含详情）+ 应用日志。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.constants import PROJECT_ROOT
from app.llm.call_log import list_llm_log_files, read_llm_calls


class LogView(QWidget):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.project_id = workspace.project.id
        self.data_dir = workspace.cfg.data_dir
        self._records: list[dict] = []

        self.tabs = QTabWidget()
        self._build_calls_tab()
        self._build_app_log_tab()
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)

    # ---------------- LLM 调用历史（文件，按天） ----------------
    def _build_calls_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        top = QHBoxLayout()
        top.addWidget(QLabel("日期："))
        self.date_combo = QComboBox()
        top.addWidget(self.date_combo)
        top.addWidget(QLabel("状态："))
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部", "")
        self.status_combo.addItem("成功", "ok")
        self.status_combo.addItem("失败", "failed")
        top.addWidget(self.status_combo)
        top.addStretch(1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._load_calls)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        splitter = QSplitter()
        self.calls_list = QListWidget()
        self.calls_list.currentItemChanged.connect(self._on_select_call)
        splitter.addWidget(self.calls_list)
        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        self.detail_view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        splitter.addWidget(self.detail_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        lay.addWidget(splitter)

        self.date_combo.currentIndexChanged.connect(self._load_calls)
        self.status_combo.currentIndexChanged.connect(self._load_calls)
        self.tabs.addTab(tab, "LLM 调用历史")

    def _load_calls(self) -> None:
        # 加载日期列表（仅首次或刷新时）
        files = list_llm_log_files(self.data_dir)
        if self.date_combo.count() == 0 or self.date_combo.property("file_count") != len(files):
            self.date_combo.blockSignals(True)
            self.date_combo.clear()
            for f in files:
                self.date_combo.addItem(f.stem, f)  # stem = 2026-08-15
            self.date_combo.setProperty("file_count", len(files))
            self.date_combo.blockSignals(False)

        path = self.date_combo.currentData()
        status = self.status_combo.currentData() or None
        if path is None:
            self.calls_list.clear()
            self.detail_view.clear()
            self.calls_list.addItem("（暂无 LLM 调用记录——先用 AI 功能生成一次）")
            return
        self._records = read_llm_calls(path, status_filter=status)
        self.calls_list.clear()
        for i, rec in enumerate(self._records):
            status_icon = {"ok": "✅", "failed": "❌", "cancelled": "⏹"}.get(rec.get("status"), "?")
            ts = (rec.get("ts") or "")[11:19]
            caller = rec.get("caller") or rec.get("module") or ""
            model = rec.get("model") or ""
            item = QListWidgetItem(
                f"{status_icon} {ts}  [{caller}] {model}  "
                f"in{rec.get('in_tokens', 0)}/out{rec.get('out_tokens', 0)}  {rec.get('duration_ms', 0)}ms"
            )
            item.setData(0x0100, i)
            self.calls_list.addItem(item)
        if not self._records:
            self.calls_list.addItem("（该日无匹配记录）")
            self.detail_view.clear()
        elif self._records:
            self.calls_list.setCurrentRow(0)

    def _on_select_call(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        idx = current.data(0x0100)
        if idx is None or idx >= len(self._records):
            return
        rec = self._records[idx]
        lines = [
            f"时间：{rec.get('ts', '')}",
            f"调用来源：{rec.get('caller', '')}",
            f"模块：{rec.get('module', '')}  |  模板：{rec.get('prompt_key', '')}",
            f"模型：{rec.get('provider', '')} / {rec.get('tier', '')} / {rec.get('model', '')}",
            f"耗时：{rec.get('duration_ms', 0)}ms  |  tokens：in={rec.get('in_tokens', 0)} out={rec.get('out_tokens', 0)}",
            f"状态：{rec.get('status', '')}",
            "",
            "——— 输入（完整）———",
            rec.get("input_summary", "") or "（无）",
            "",
            "——— 输出（完整）———",
            rec.get("output_summary", "") or "（无）",
        ]
        if rec.get("error"):
            lines += ["", "——— 错误 ———", rec["error"]]
        self.detail_view.setPlainText("\n".join(lines))

    # ---------------- 应用日志 ----------------
    def _build_app_log_tab(self) -> None:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        top = QHBoxLayout()
        top.addWidget(QLabel("仅显示错误："))
        self.err_only = QComboBox()
        self.err_only.addItem("否", False)
        self.err_only.addItem("是", True)
        self.err_only.currentIndexChanged.connect(self._load_app_log)
        top.addWidget(self.err_only)
        top.addStretch(1)
        self.btn_refresh_log = QPushButton("刷新")
        self.btn_refresh_log.clicked.connect(self._load_app_log)
        top.addWidget(self.btn_refresh_log)
        lay.addLayout(top)
        self.app_log_view = QPlainTextEdit()
        self.app_log_view.setReadOnly(True)
        self.app_log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        lay.addWidget(self.app_log_view)
        self.tabs.addTab(tab, "应用日志")

    def _load_app_log(self) -> None:
        log_file = PROJECT_ROOT / "data" / "logs" / "app.log"
        if not log_file.exists():
            self.app_log_view.setPlainText("（暂无日志文件）")
            return
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if self.err_only.currentData():
            lines = [l for l in lines if "ERROR" in l or "Traceback" in l or "Exception" in l]
        self.app_log_view.setPlainText("\n".join(lines[-500:]))
        # 自动滚动到最后一行（最新日志），不用手动拖到底
        cursor = self.app_log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.app_log_view.setTextCursor(cursor)
        self.app_log_view.ensureCursorVisible()

    def refresh(self) -> None:
        self._load_calls()
        self._load_app_log()
