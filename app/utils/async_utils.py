"""异步辅助：在 qasync 事件循环中运行协程并回调 UI 线程。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logger import get_logger

_log = get_logger("async_utils")


def run_async(
    coro: Awaitable,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> asyncio.Task:
    """把协程调度到事件循环；完成后回调。异常一律记日志（错误捕获可见）。"""

    async def _runner():
        try:
            result = await coro
        except Exception as exc:  # noqa: BLE001 - 统一上抛给 UI
            _log.exception("异步任务失败: %s", exc)  # 错误记入应用日志（含 traceback）
            if on_error:
                try:
                    on_error(exc)
                except Exception:
                    _log.exception("on_error 回调自身也失败")  # 回调崩溃不能无声
            else:
                raise
        else:
            if on_done:
                try:
                    on_done(result)
                except Exception:
                    # 回调崩溃（如渲染计划卡片遇 null 字段）——必须留痕，否则 UI 无声失败
                    _log.exception("on_done 回调失败（结果类型: %s）", type(result).__name__)

    loop = asyncio.get_event_loop()
    return loop.create_task(_runner())
