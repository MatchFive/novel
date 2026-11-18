"""issues 台账：`/issues` 命令与 CanonAudit 共用的数据面。

活动文件位于 `<root>/.harness/issues/<id>.json`（四位序号，如 0029）；
每 issue 一个文件，写为 tmp+replace 原子写。
status：pending / resolved / ignored；`list()` 默认只看 pending。
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("run.issues")

ISSUES_DIR = Path(".harness") / "issues"

STATUSES = frozenset({"pending", "resolved", "ignored"})


@dataclass
class Issue:
    id: str
    source: str
    title: str
    detail: str
    quote: str | None
    status: str
    created_at: str
    chapter: int | None = None    # P5：审计候选关联章号；旧落盘文件无此键读 None


class IssuesStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / ISSUES_DIR

    # ------------------------------------------------------------------ 写

    def add(self, source: str, title: str, detail: str, quote: str | None = None,
            *, chapter: int | None = None) -> Issue:
        """新建 pending issue，id 为目录内最大四位序号 +1（从 0001 起）。

        ``chapter`` 为可选关联章号（P5 审计候选）；不传为 None，签名向后兼容。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        nxt = max((int(p.stem) for p in self.dir.glob("*.json") if p.stem.isdigit()), default=0) + 1
        issue = Issue(
            id=f"{nxt:04d}",
            source=source,
            title=title,
            detail=detail,
            quote=quote,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
            chapter=chapter,
        )
        self._save(issue)
        return issue

    def set_status(self, id: str, status: str) -> Issue:
        """更新状态；未知 id 抛 KeyError，非法 status 抛 ValueError。"""
        if status not in STATUSES:
            raise ValueError(f"非法 status: {status}")
        issue = self._load_one(id)
        if issue is None:
            raise KeyError(f"未知 issue id: {id}")
        issue.status = status
        self._save(issue)
        return issue

    def _save(self, issue: Issue) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{issue.id}.json"
        payload = {
            "id": issue.id,
            "source": issue.source,
            "title": issue.title,
            "detail": issue.detail,
            "quote": issue.quote,
            "status": issue.status,
            "created_at": issue.created_at,
            "chapter": issue.chapter,
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ 读

    def list(self, status: str | None = "pending") -> list[Issue]:
        """按 status 过滤；status=None 返回全部。按 id 升序。"""
        issues = [self._load_one(p.stem) for p in sorted(self.dir.glob("*.json"))] if self.dir.exists() else []
        issues = [i for i in issues if i is not None]
        if status is not None:
            issues = [i for i in issues if i.status == status]
        return issues

    def next_pending(self) -> Issue | None:
        """最老的 pending；无则 None。"""
        pending = self.list("pending")
        return pending[0] if pending else None

    def one(self, id: str) -> Issue | None:
        """按 id 读一条（**任意状态**）；不存在 / 坏档 → None（与 list 同容错口径，不崩）。"""
        return self._load_one(id)

    def _load_one(self, id: str) -> Issue | None:
        """读一条 issue；**文件坏**（JSON 坏 / **缺键**）→ 返回 None（跳过，不崩）。

        缺键也算坏档（2026-11-15 独立审核 D6）：台账是**用户可手改**的文件，
        缺一个 ``detail`` 不该让 ``/issues``、``run_check`` 的 supersede、``/audit`` 落库、
        ``/fix`` 分组**全部**中招（KeyError 直穿）。
        """
        path = self.dir / f"{id}.json"
        if not path.exists():
            return None
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("台账条目读取失败（已跳过）：%s（%s）", path, exc)
            return None
        try:
            return Issue(
                id=obj["id"],
                source=obj["source"],
                title=obj["title"],
                detail=obj["detail"],
                quote=obj.get("quote"),
                status=obj["status"],
                created_at=obj.get("created_at", ""),
                chapter=obj.get("chapter"),
            )
        except (KeyError, TypeError) as exc:
            log.warning("台账条目缺字段（已跳过）：%s（%s）", path, exc)
            return None
