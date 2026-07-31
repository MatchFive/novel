"""pywebview 桌面启动器：拉起 FastAPI 服务并打开本地窗口。"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

try:
    import webview  # pywebview
except ImportError:  # pragma: no cover
    webview = None

from app.config import settings
from app.logging_config import setup_logging

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8765
APP_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"


def _run_server() -> None:
    from app.main import create_app

    app = create_app()
    config = uvicorn.Config(
        app, host=BACKEND_HOST, port=BACKEND_PORT, log_level="info",
    )
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


def _wait_for_server(timeout: float = 30.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=2.0) as c:
                r = c.get(f"{APP_URL}/health")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> None:
    setup_logging(settings.log_level, Path(settings.log_dir))
    launcher_logger = logging.getLogger("launcher")
    launcher_logger.info("启动器启动，数据目录: %s", settings.log_dir)

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    if not _wait_for_server():
        launcher_logger.error("后端启动失败，回退到系统浏览器。")
        webbrowser.open(APP_URL)
        return

    launcher_logger.info("后端已就绪，打开窗口。")

    if webview is None:
        launcher_logger.warning("pywebview 不可用，回退到系统浏览器。")
        webbrowser.open(APP_URL)
        return

    try:
        webview.create_window(
            title=settings.app_name,
            url=APP_URL,
            width=1280,
            height=800,
            min_size=(960, 640),
        )
        webview.start(gui="edgechromium")
    except Exception:
        launcher_logger.exception("桌面窗口启动失败，回退到系统浏览器。")
        webbrowser.open(APP_URL)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
