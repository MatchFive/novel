"""逐步定位：step A 与 debug_ckpt 相同。"""
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
    print("step A start", flush=True)
    conn = await aiosqlite.connect(str(tmp / "w.db"))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    print("step A saver setup done", flush=True)
    g = StateGraph(S)
    g.add_node("a", node)
    g.set_entry_point("a")
    g.add_edge("a", END)
    compiled = g.compile(checkpointer=saver)
    out = await compiled.ainvoke({"x": 0}, {"configurable": {"thread_id": "t1"}})
    print("step A ainvoke ok:", out, flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(m(), 20))
    except Exception:
        traceback.print_exc()
