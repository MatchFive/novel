"""统一日志配置：文件回滚 + 来源分离，便于打包后可执行文件排错。"""
from __future__ import annotations

import logging
import logging.config
from pathlib import Path


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 单个日志文件 10MB
_BACKUP_COUNT = 5


def setup_logging(level: str, log_dir: Path) -> None:
    """配置 root / uvicorn / frontend / launcher 日志器，输出到 log_dir 下各自文件。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": _LOG_FORMAT,
                "datefmt": _DATE_FORMAT,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": level,
            },
            "backend_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "level": level,
                "filename": str(log_dir / "backend.log"),
                "maxBytes": _MAX_BYTES,
                "backupCount": _BACKUP_COUNT,
                "encoding": "utf-8",
            },
            "frontend_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "level": level,
                "filename": str(log_dir / "frontend.log"),
                "maxBytes": _MAX_BYTES,
                "backupCount": _BACKUP_COUNT,
                "encoding": "utf-8",
            },
            "launcher_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "level": level,
                "filename": str(log_dir / "launcher.log"),
                "maxBytes": _MAX_BYTES,
                "backupCount": _BACKUP_COUNT,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "": {
                "handlers": ["console", "backend_file"],
                "level": level,
                "propagate": False,
            },
            "uvicorn": {
                "handlers": ["console", "backend_file"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console", "backend_file"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console", "backend_file"],
                "level": level,
                "propagate": False,
            },
            "frontend": {
                "handlers": ["console", "frontend_file"],
                "level": level,
                "propagate": False,
            },
            "launcher": {
                "handlers": ["console", "launcher_file"],
                "level": level,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(config)


def get_frontend_logger() -> logging.Logger:
    return logging.getLogger("frontend")


def get_launcher_logger() -> logging.Logger:
    return logging.getLogger("launcher")
