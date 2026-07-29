"""WorkerManager: discover workers from JSON configs and optional Python overrides."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.agents.harness.models import WorkerMetadata
from app.agents.harness.worker_base import WorkerBase

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent / "workers" / "configs"


class WorkerManager:
    _instance: "WorkerManager | None" = None

    def __new__(cls) -> "WorkerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._workers: dict[str, tuple[type[WorkerBase], WorkerMetadata]] = {}
        self._initialized = True
        self._load_all()

    def _load_all(self) -> None:
        for path in sorted(CONFIG_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                metadata = WorkerMetadata(**data)
                worker_cls = self._resolve_class(metadata.worker_name)
                self._workers[metadata.worker_name] = (worker_cls, metadata)
            except Exception:
                logger.exception("Failed to load worker config %s", path.name)

    def _resolve_class(self, worker_name: str) -> type[WorkerBase]:
        """Try to import an overriding Python class; otherwise return WorkerBase."""
        module_name = f"app.agents.harness.workers.{worker_name}_worker"
        class_name = self._to_camel(worker_name) + "Worker"
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            if issubclass(cls, WorkerBase):
                return cls
        except Exception:
            pass
        return WorkerBase

    @staticmethod
    def _to_camel(snake: str) -> str:
        return "".join(part.capitalize() for part in snake.split("_"))

    def list_workers(self) -> list[WorkerMetadata]:
        return [metadata for _, metadata in self._workers.values()]

    def get_worker_class(self, worker_name: str) -> type[WorkerBase]:
        cls, _ = self._workers[worker_name]
        return cls

    def get_metadata(self, worker_name: str) -> WorkerMetadata:
        _, metadata = self._workers[worker_name]
        return metadata

    def create_worker(
        self,
        worker_name: str,
        db,
        llm,
        recursive_limit: int,
    ) -> WorkerBase:
        cls, metadata = self._workers[worker_name]
        return cls(db, llm, recursive_limit, metadata=metadata, timeout=metadata.timeout)

    def available_workers(self) -> list[str]:
        return list(self._workers.keys())


def get_worker_manager() -> WorkerManager:
    return WorkerManager()
