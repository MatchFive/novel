"""LLM 调用文件日志：按天一个 JSONL 文件，记录完整调用详情（不增大数据库负载）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _log_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "logs" / "llm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_llm_call(data_dir: Path, record: dict) -> None:
    """追加一条 LLM 调用记录到当日 JSONL 文件。"""
    record = dict(record)
    record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    path = _log_dir(data_dir) / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # 写文件失败不影响主流程


def list_llm_log_files(data_dir: Path) -> list[Path]:
    """返回所有 LLM 日志文件（按日期倒序）。"""
    d = _log_dir(data_dir)
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.name, reverse=True)
    return files


def read_llm_calls(path: Path, status_filter: str | None = None, limit: int = 500) -> list[dict]:
    """读取某天的 LLM 调用记录（倒序，新在前）。"""
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.reverse()
    if status_filter:
        rows = [r for r in rows if r.get("status") == status_filter]
    return rows[:limit]
