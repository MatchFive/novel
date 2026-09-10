"""最小 checkpointer 图测试。"""
import asyncio
import tempfile
import traceback
from pathlib import Path


async def m():
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    class S(TypedDict, total=False):
        x: int

    async def node(s):
        return {**s, "x": 1}

    tmp = Path(tempfile.mkdtemp())
    print("connecting", flush=True)
    conn = await aiosqlite.connect(str(tmp / "w.db"))
    print("conn ok", flush=True)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    print("setup ok", flush=True)
    g = StateGraph(S)
    g.add_node("a", node)
    g.set_entry_point("a")
    g.add_edge("a", END)
    compiled = g.compile(checkpointer=saver)
    print("compiled ok", flush=True)
    cfg = {"configurable": {"thread_id": "t1"}}
    out = await compiled.ainvoke({"x": 0}, cfg)
    print("ainvoke ok:", out, flush=True)
    await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(m(), 20))
    except Exception:
        traceback.print_exc()
