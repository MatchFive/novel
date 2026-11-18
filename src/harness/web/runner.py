"""web 侧的 :class:`harness.session.Runner` 实现：把 run 绑到 FastAPI 所在事件循环。

与 repl 的 ``_ThreadRunner``（同步世界：TTY 阻塞 / 非 TTY daemon 线程）相对：
web 世界**本就有事件循环在跑**——``launch`` 用 ``asyncio.create_task(drive_run(...))``
把 run 挂上去，与 AgentLoop **同一个事件循环**（LLM 客户端的 httpx 连接池绑定
loop，跨 loop 复用会炸；**禁** ``asyncio.run()``）。

Task 4 已钉死的约定：``/state`` 的 ``running`` 探测走 ``runner.running`` 属性
（布尔）——本类以 ``running`` property 提供该口径，协议要求的 ``alive()`` 同义。
"""
from __future__ import annotations

import asyncio
import logging

from harness.run.state import RunState
from harness.session import Components, RunSession, drive_run

log = logging.getLogger("harness.web.runner")


class AsyncRunner:
    """单 run 守卫 + 驱动协议（web 实现）：内部持一个 asyncio.Task。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        """Task 4 ``/state`` 的 ``running`` 探测口径：是否有未完成的 run 任务。"""
        return self.alive()

    def alive(self) -> bool:
        """当前是否有活着的 run（守卫判据）：内部 asyncio.Task 存在且未完成。"""
        return self._task is not None and not self._task.done()

    def launch(self, session: RunSession, state: RunState, comp: Components, *,
               intake: tuple[str, list[str], bool] | None = None) -> None:
        """``asyncio.create_task(drive_run(...))``——与 AgentLoop 同一事件循环。

        必须在有运行中事件循环的上下文调用（FastAPI ``async def`` 路由内）；
        返回即「驱动安排完毕」。驱动异常经 wrapper 落日志（不复查会
        「Task exception was never retrieved」）。
        """
        self._task = asyncio.create_task(self._drive(session, state, comp, intake=intake))

    async def _drive(self, session: RunSession, state: RunState, comp: Components, *,
                     intake: tuple[str, list[str], bool] | None = None) -> None:
        try:
            await drive_run(session, state, comp, intake=intake)
        except Exception:
            log.exception("web run 驱动失败（status=%s）", state.status)
