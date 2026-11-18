"""harness CLI 入口（拍板⑪，dsh 式启动）。

用法：``harness`` 打开项目选择器；``harness <路径>`` 直达项目进入 REPL；
``harness web`` 起本机只读 Web UI（127.0.0.1）。
非交互环境（stdin 非 TTY）且无路径/子命令时返回 2 并提示用法。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.config import load_settings
from harness.projects import open_project
from harness.repl import run_repl, select_project
from harness.store.frontmatter import FileFormatError
from harness.store.project import ProjectStore


def _ensure_utf8_stdio() -> None:
    """Windows GBK 控制台兜底（2026-11-18 验收 Critical ③）：进程内等价于 PYTHONUTF8=1。

    ``harness web`` / REPL 的真终端常见 cp936（GBK）——``_record_step`` 的 💭 print
    等 Unicode 输出会抛 UnicodeEncodeError 杀掉驱动任务。入口处把 sys.stdout/stderr
    reconfigure 成 utf-8 + errors="replace"（编码不支持的字符降级为 ? 而非抛异常）。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 非 TextIOWrapper（如 StringIO/重定向管道包装）无从 reconfigure
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass  # 已关闭/只读包装等：保持原状，不挡启动


def _web_main(argv: list[str]) -> int:
    """``harness web``：FastAPI 骨架 + 只读 API（Task 4）。只绑 127.0.0.1，不鉴权。"""
    import threading
    import webbrowser

    parser = argparse.ArgumentParser(
        prog="harness web",
        description="启动本机 Web UI（127.0.0.1，无鉴权；--no-browser 不自动开浏览器）。",
    )
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--library", help="测试钩子：覆盖 settings.library_root")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.library is not None:
        settings.library_root = Path(args.library)

    from harness.web.app import create_app

    app = create_app(settings)
    if not args.no_browser:
        # uvicorn.run 阻塞：起服务后延迟开浏览器（server 绑定在毫秒级完成）。
        url = f"http://127.0.0.1:{args.port}"
        timer = threading.Timer(0.5, lambda: webbrowser.open(url))
        timer.daemon = True  # server 启动失败退出时不挡进程结束
        timer.start()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()  # GBK 控制台兜底（💭 等 Unicode 输出不再杀进程）
    if argv is None:
        argv = sys.argv[1:]
    # web 子命令：路径形参与 --port/--no-browser 不兼容，单独 argparse，互不干扰。
    if argv and argv[0] == "web":
        return _web_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="harness",
        description="小说家 harness：启动 = 项目选择器；harness <路径> 直达项目。",
    )
    parser.add_argument("path", nargs="?", help="项目路径（直达，跳过选择器）")
    parser.add_argument("--library", help="测试钩子：覆盖 settings.library_root")
    parser.add_argument("--resume", action="store_true",
                        help="启动时若 state 非终态直接续跑（重建记忆接回主循环）")
    args = parser.parse_args(argv)

    if args.path is None and not sys.stdin.isatty():
        print(
            "harness 需要交互式终端（TTY）才能打开项目选择器；"
            "非交互环境请直接指定项目路径：harness <路径>",
            file=sys.stderr,
        )
        return 2

    # 项目级配置不自动发现（Task 3 ruling）：拿到路径后显式传入。
    if args.path is not None:
        settings = load_settings(project_path=Path(args.path) / ".harness" / "config.toml")
    else:
        settings = load_settings()
    if args.library is not None:
        settings.library_root = Path(args.library)

    try:
        if args.path is not None:
            project = open_project(Path(args.path))
        else:
            project = select_project(settings)
        run_repl(project, ProjectStore(project.path), resume=args.resume)
    except FileNotFoundError:
        print(f"harness: 项目路径不存在或缺少 README.md：{args.path}"
              f"（请检查路径；用法：harness <项目路径>）", file=sys.stderr)
        return 1
    except FileFormatError as exc:
        print(f"harness: 项目文件损坏：{exc.path}（{exc.reason}）"
              f"（原文件已备份为 .bak，修复后再试）", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
