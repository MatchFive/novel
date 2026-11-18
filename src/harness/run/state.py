"""state.json：RunState 落盘/恢复（设计 §E.3 schema）。

活动文件位于 `<root>/.harness/state.json`；写为 tmp+replace 原子写。
schema：`status` / `brief`（raw/premise/constraints/deliverables/plan_arcs/write_target/co_design）
/ `stats`（step/effective_steps/total_steps/step_kinds/token_used/token_baseline/budget_windows/
step_plan/retry_queue/skip_list/plan_confirmed/destructive_ok/auto_resumes）/ `updated_at` ISO8601。
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVENTS_DIR = ".harness"
STATE_FILE = "state.json"

log = logging.getLogger("run.state")

TERMINAL_STATUSES = frozenset({"done", "failed", "aborted"})


def fresh_stats() -> dict:
    """E.3 stats 模板工厂：每次调用返回全新 dict，绝不共享可变模板。"""
    return {
        "step": 0,
        "effective_steps": 0,
        "total_steps": 0,
        "step_kinds": {},
        "token_used": 0,
        "token_baseline": 0,
        "budget_windows": 0,
        "step_plan": [],
        "retry_queue": [],
        "skip_list": [],
        "plan_confirmed": False,
        "destructive_ok": False,
        "auto_resumes": 0,
    }


@dataclass
class RunState:
    status: str
    brief: dict
    stats: dict = field(default_factory=fresh_stats)
    updated_at: str = ""

    @classmethod
    def new(cls, brief_raw: str, deliverables: list[str]) -> "RunState":
        """初始状态：status="planning"，stats 全零，brief 仅 raw/deliverables 已知。"""
        brief = {
            "raw": brief_raw,
            # **goal**（P10）：需求解析产出的「一句话目标」——验收链 ``goals`` 槽最贴切的
            # 内容。旧 state.json 没有这个键 → 读取时回落（见 ``RunState.load``）。
            "goal": None,
            "premise": None,
            "constraints": [],
            "deliverables": list(deliverables),
            "plan_arcs": None,
            "write_target": None,
            "co_design": False,
        }
        return cls(status="planning", brief=brief, stats=fresh_stats())

    # ------------------------------------------------------------------ 写

    def save(self, root: Path) -> None:
        """写 `<root>/.harness/state.json`；tmp+replace 原子写；updated_at 取当前 ISO8601。

        **tmp 文件名带唯一后缀**（2026-11-17，独立复审 P16-4 修完带出的并发缺陷）：
        后台 run 跑着的时候，REPL 的 `/pause`、`/abort` **也会写同一个 state.json**
        → 两者**同时**写同一个 ``state.json.tmp`` → Windows 上先写的那家把文件**开着**，
        后写的 `write_text` → **PermissionError** → run 崩掉。
        → 每次写用**唯一**的 tmp 名（pid + 线程 id）→ 互不干扰；``os.replace`` 仍是原子的。
        """
        self.updated_at = datetime.now(UTC).isoformat()
        path = Path(root) / EVENTS_DIR / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": self.status,
            "brief": self.brief,
            "stats": self.stats,
            "updated_at": self.updated_at,
        }
        tmp = path.with_name(f"{STATE_FILE}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        finally:
            # 失败时别留残file（下次写会覆盖，但残file会让人以为「写了一半」）
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ 读

    @classmethod
    def load(cls, root: Path) -> "RunState | None":
        """读取 `<root>/.harness/state.json`；文件缺失或损坏（坏 JSON / 非 UTF-8）
        均返回 None——损坏按缺失同一恢复路径处理，不向上抛。

        **向后兼容**（P10）：旧 state.json 的 brief 里没有 ``goal`` 键 → 补一个 None
        （不报错、不改写旧文件；消费方按「无 goal」回落）。
        """
        path = Path(root) / EVENTS_DIR / STATE_FILE
        if not path.exists():
            return None
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("state.json 读取失败（按无状态处理）：%s（%s）", path, exc)
            return None
        # **结构校验**（P13）：JSON 合法但**形状不对**（缺 status/brief/stats，或类型不对）
        # → 当坏档处理（返回 None），**不**让 KeyError/TypeError 穿出去——否则手改坏 /
        # 别的程序写歪的 state.json 会让 ``--resume`` 带栈崩掉（设计 §2.1 的续跑路径）。
        if not isinstance(obj, dict):
            log.warning("state.json 结构异常（不是对象，按无状态处理）：%s", path)
            return None
        status = obj.get("status")
        brief = obj.get("brief")
        stats = obj.get("stats")
        if (not isinstance(status, str) or not isinstance(brief, dict)
                or not isinstance(stats, dict)):
            log.warning("state.json 结构异常（缺字段或类型不对，按无状态处理）：%s", path)
            return None
        if "goal" not in brief:
            brief = {**brief, "goal": None}     # 旧文件（P10 之前）没有 goal → 补 None
        return cls(
            status=status,
            brief=brief,
            stats=stats,
            updated_at=str(obj.get("updated_at") or ""),
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
