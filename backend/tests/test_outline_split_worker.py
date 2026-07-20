"""OutlineSplitWorker 与 temp_id 透传测试。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app import repositories as repo
from app.agents.harness.nodes.aggregator import aggregate
from app.agents.harness.state import ChangeRecord, make_change
from app.agents.harness.workers import OutlineSplitWorker


@pytest.fixture
def fake_llm():
    class _LLM:
        def __init__(self, response):
            self.response = response

        async def parse_llm_json(self, messages):
            return self.response

    return _LLM


@pytest.fixture
def fake_db():
    return AsyncMock()


@pytest.mark.anyio
async def test_outline_split_worker_broad_target(fake_db, fake_llm, monkeypatch):
    target = {
        "id": "broad-1",
        "type": "broad",
        "title": "总纲",
        "content": "很长总纲",
    }

    async def _list_outlines(db, project_id):
        return [target]

    monkeypatch.setattr(
        "app.agents.harness.workers.repo.list_outlines", _list_outlines
    )

    response = {
        "periods": [
            {
                "title": "第一时期",
                "summary": "第一时期概述",
                "volumes": [
                    {
                        "title": "第一卷",
                        "content": "第一卷内容",
                        "chapter_start": 1,
                        "chapter_end": 10,
                    }
                ],
            }
        ]
    }
    worker = OutlineSplitWorker(fake_db, fake_llm(response), 5)
    result = await worker.run("拆成几卷", {"project_id": "p1", "entity_id": "broad-1"})

    assert result.get("stage") == "outline_split"
    changes = result.get("changes", [])
    assert len(changes) == 2

    period_change = changes[0]
    assert period_change["action"] == "add"
    assert period_change["temp_id"] == "temp:period:0"
    assert period_change["fields"]["type"] == "period"
    assert period_change["fields"]["parent_id"] == "broad-1"

    volume_change = changes[1]
    assert volume_change["action"] == "add"
    assert volume_change["fields"]["type"] == "volume"
    assert volume_change["fields"]["parent_id"] == "temp:period:0"
    assert volume_change["fields"]["chapter_start"] == 1
    assert volume_change["fields"]["chapter_end"] == 10


@pytest.mark.anyio
async def test_outline_split_worker_period_target(fake_db, fake_llm, monkeypatch):
    target = {
        "id": "period-1",
        "type": "period",
        "title": "第一时期",
        "content": "时期概述",
    }

    async def _list_outlines(db, project_id):
        return [target]

    monkeypatch.setattr(
        "app.agents.harness.workers.repo.list_outlines", _list_outlines
    )

    response = {
        "summary": "改写后的时期概述",
        "volumes": [
            {
                "title": "第一卷",
                "content": "第一卷内容",
                "chapter_start": 11,
                "chapter_end": 20,
            }
        ],
    }
    worker = OutlineSplitWorker(fake_db, fake_llm(response), 5)
    result = await worker.run(
        "把这个时期拆成几卷", {"project_id": "p1", "entity_id": "period-1"}
    )

    assert result.get("stage") == "outline_split"
    changes = result.get("changes", [])
    assert len(changes) == 2

    update_change = changes[0]
    assert update_change["action"] == "update"
    assert update_change["entity_id"] == "period-1"
    assert update_change["fields"]["type"] == "period"
    assert update_change["fields"]["content"] == "改写后的时期概述"

    volume_change = changes[1]
    assert volume_change["action"] == "add"
    assert volume_change["fields"]["type"] == "volume"
    assert volume_change["fields"]["parent_id"] == "period-1"
    assert volume_change["fields"]["chapter_start"] == 11
    assert volume_change["fields"]["chapter_end"] == 20


@pytest.mark.anyio
async def test_outline_split_worker_missing_context(fake_db, fake_llm):
    worker = OutlineSplitWorker(fake_db, fake_llm({}), 5)
    result = await worker.run("拆成几卷", {"project_id": "p1"})
    assert result.get("error") == "缺少 project_id 或 entity_id"

    result = await worker.run("拆成几卷", {"entity_id": "e1"})
    assert result.get("error") == "缺少 project_id 或 entity_id"


@pytest.mark.anyio
async def test_outline_split_worker_target_not_found(fake_db, fake_llm, monkeypatch):
    async def _list_outlines(db, project_id):
        return []

    monkeypatch.setattr(
        "app.agents.harness.workers.repo.list_outlines", _list_outlines
    )
    worker = OutlineSplitWorker(fake_db, fake_llm({}), 5)
    result = await worker.run("拆成几卷", {"project_id": "p1", "entity_id": "missing"})
    assert result.get("error") == "目标大纲不存在"


@pytest.mark.anyio
async def test_outline_split_worker_invalid_llm_response(fake_db, fake_llm, monkeypatch):
    target = {"id": "broad-1", "type": "broad", "title": "总纲", "content": "x"}

    async def _list_outlines(db, project_id):
        return [target]

    monkeypatch.setattr(
        "app.agents.harness.workers.repo.list_outlines", _list_outlines
    )
    worker = OutlineSplitWorker(fake_db, fake_llm("not a dict"), 5)
    result = await worker.run("拆成几卷", {"project_id": "p1", "entity_id": "broad-1"})
    assert result.get("error") == "无法解析拆分结果"


@pytest.mark.anyio
async def test_outline_split_worker_legacy_root_under_broad(fake_db, fake_llm, monkeypatch):
    """legacy 类型（如'主线卷'）且无父级时，应归到已有总纲下作为时期并拆卷。"""
    broad_root = {"id": "broad-1", "type": "broad", "title": "总纲", "content": "x"}
    legacy = {"id": "legacy-1", "type": "主线卷", "title": "开荒期", "content": "很长"}

    async def _list_outlines(db, project_id):
        return [broad_root, legacy]

    monkeypatch.setattr(
        "app.agents.harness.workers.repo.list_outlines", _list_outlines
    )

    response = {
        "summary": "开荒期概述",
        "volumes": [
            {"title": "第一卷", "content": "内容", "chapter_start": 1, "chapter_end": 10}
        ],
    }
    worker = OutlineSplitWorker(fake_db, fake_llm(response), 5)
    result = await worker.run("拆成几卷", {"project_id": "p1", "entity_id": "legacy-1"})

    changes = result.get("changes", [])
    assert len(changes) == 2
    update_change = changes[0]
    assert update_change["action"] == "update"
    assert update_change["entity_id"] == "legacy-1"
    assert update_change["fields"]["type"] == "period"
    assert update_change["fields"]["parent_id"] == "broad-1"

    volume_change = changes[1]
    assert volume_change["action"] == "add"
    assert volume_change["fields"]["type"] == "volume"
    assert volume_change["fields"]["parent_id"] == "legacy-1"


def test_make_change_carries_temp_id():
    cr = make_change(
        project_id="p1",
        action="add",
        entity_type="outline",
        after={"title": "x"},
        temp_id="temp:broad:1",
    )
    assert isinstance(cr, ChangeRecord)
    assert cr.temp_id == "temp:broad:1"


def test_aggregate_preserves_temp_id():
    worker_results = [
        {
            "worker": "outline_split",
            "stage": "outline_split",
            "changes": [
                {
                    "action": "add",
                    "temp_id": "temp:period:0",
                    "fields": {"type": "period", "parent_id": "broad-1"},
                },
                {
                    "action": "add",
                    "fields": {
                        "type": "volume",
                        "parent_id": "temp:period:0",
                        "chapter_start": 1,
                    },
                },
            ],
        }
    ]
    records = aggregate("p1", worker_results)
    assert len(records) == 2
    assert records[0].entity_type == "outline"
    assert records[0].temp_id == "temp:period:0"
    assert records[0].after["parent_id"] == "broad-1"
    assert records[1].entity_type == "outline"
    assert records[1].after["parent_id"] == "temp:period:0"
    assert records[1].temp_id is None
