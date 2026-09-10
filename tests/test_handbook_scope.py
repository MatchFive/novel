"""教程库公用/本项目测试：归属转移、激活复制、检索去重（不调用 LLM）。运行：python tests/test_handbook_scope.py"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.migrations import seed_defaults  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.handbook_service import HandbookService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


def main() -> int:
    try:
        db = Database(Path(tempfile.mkdtemp()) / "sc.db")
        db.init_schema()
        seed_defaults(db)
        ps = ProjectService(db)
        p1 = ps.create(name="小说A", genre="玄幻", logline="a")
        p2 = ps.create(name="小说B", genre="都市", logline="b")
        svc = HandbookService(db)

        # ---- 公用教程：两个项目都能检索到 ----
        g = svc.import_text("钩子写法", "正文", "钩子前置三行内。", project_id=None)
        assert svc.search_chunks("钩子", project_id=p1.id), "公用教程应对项目A可检索"
        assert svc.search_chunks("钩子", project_id=p2.id), "公用教程应对项目B可检索"
        print("global visible to all projects OK")

        # ---- 本项目教程：其他项目不可见 ----
        d = svc.import_text("独有节奏技巧", "节奏", "快节奏独有技法。", project_id=p1.id)
        assert svc.search_chunks("独有技法", project_id=p1.id)
        assert not svc.search_chunks("独有技法", project_id=p2.id), "本项目教程不应对其他项目可检索"
        print("project-only visibility OK")

        # ---- set_scope：本项目 → 公用 → 其他项目可见 ----
        svc.set_scope(d.id, None)
        assert svc.search_chunks("独有技法", project_id=p2.id), "设为公用后其他项目应可检索"
        # 公用 → 本项目
        svc.set_scope(d.id, p2.id)
        assert not svc.search_chunks("独有技法", project_id=p1.id), "转给B后A不应再可见"
        assert svc.search_chunks("独有技法", project_id=p2.id)
        print("set_scope both ways OK")

        # ---- copy_to_project：激活副本，幂等 ----
        copy = svc.copy_to_project(g.id, p1.id)
        assert copy.project_id == p1.id and copy.title == g.title
        assert len(svc.list_chunks(copy.id)) == len(svc.list_chunks(g.id)), "副本应含全部切片"
        again = svc.copy_to_project(g.id, p1.id)
        assert again.id == copy.id, "重复激活应返回既有副本"
        n_docs = len([x for x in svc.list_docs(p1.id) if x.title == g.title])
        assert n_docs == 2, "应为 公用原件 + 本项目副本 各一份"
        print("copy_to_project + idempotent OK")

        # ---- 检索去重：公用原件 + 项目副本同时启用时，相同切片只出现一次 ----
        hits = svc.search_chunks("钩子", project_id=p1.id, limit=10)
        keys = [(c.title, c.content) for c in hits]
        assert len(keys) == len(set(keys)), f"检索结果应去重: {keys}"
        # 本项目副本排前面
        assert hits[0].doc_id == copy.id, "本项目副本应优先"
        print("search dedupe + project-first OK")

        # ---- 停用副本不影响公用原件 ----
        svc.set_enabled(copy.id, False)
        hits2 = svc.search_chunks("钩子", project_id=p1.id)
        assert hits2 and all(c.doc_id != copy.id for c in hits2), "副本停用后由公用原件提供检索"
        print("independent enable/disable OK")

        print("ALL HANDBOOK SCOPE TESTS PASSED")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
