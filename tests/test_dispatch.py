import asyncio
from unittest.mock import AsyncMock

import pytest

from registry.dispatch import DispatchEngine
from registry.main import _perform_dispatch, _perform_dispatch_async


@pytest.mark.asyncio
async def test_resolve_updates_task_result_success(monkeypatch):
    engine = DispatchEngine()
    future = asyncio.get_running_loop().create_future()
    engine.pending["task-1"] = future
    update_task_result = AsyncMock()
    monkeypatch.setattr("registry.dispatch.update_task_result", update_task_result)

    result = {"status": "success", "data": {"value": 1}}
    await engine.resolve("task-1", result)

    update_task_result.assert_awaited_once_with("task-1", "success", result)
    assert future.done()
    assert future.result() == result


@pytest.mark.asyncio
async def test_resolve_updates_task_result_error(monkeypatch):
    engine = DispatchEngine()
    future = asyncio.get_running_loop().create_future()
    engine.pending["task-2"] = future
    update_task_result = AsyncMock()
    monkeypatch.setattr("registry.dispatch.update_task_result", update_task_result)

    await engine.resolve("task-2", {"status": "error", "message": "boom"})

    update_task_result.assert_awaited_once()
    args = update_task_result.await_args.args
    assert args[0] == "task-2"
    assert args[1] == "error"
    assert args[2]["error"] == "boom"
    assert future.done()
    assert future.result()["status"] == "error"


@pytest.mark.asyncio
async def test_dispatch_raises_when_machine_offline():
    engine = DispatchEngine()
    with pytest.raises(RuntimeError, match="offline|绂荤嚎|当前离线"):
        await engine.dispatch("missing-machine", "invoice_ocr", {})


@pytest.mark.asyncio
async def test_perform_dispatch_returns_error_when_no_online_machine(monkeypatch):
    monkeypatch.setattr("registry.main.get_online_machines_for_rpa", AsyncMock(return_value=[]))

    result = await _perform_dispatch("invoice_ocr", {})

    assert result["status"] == "error"
    assert "No available machines" in result["message"]


@pytest.mark.asyncio
async def test_perform_dispatch_rejects_explicit_machine_without_rpa_support(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_machine",
        AsyncMock(return_value={"id": "machine-a", "status": "online", "rpas": ["send_email"]}),
    )
    dispatch_mock = AsyncMock(return_value={"status": "success", "task_id": "task-1"})
    monkeypatch.setattr("registry.main.engine.dispatch", dispatch_mock)

    result = await _perform_dispatch("invoice_ocr", {}, machine_id="machine-a")

    assert result["status"] == "error"
    assert result["message"] == "Machine machine-a does not support RPA invoice_ocr"
    dispatch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_perform_dispatch_async_rejects_explicit_machine_without_rpa_support(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_machine",
        AsyncMock(return_value={"id": "machine-a", "status": "online", "rpas": ["send_email"]}),
    )
    dispatch_async_mock = AsyncMock(return_value={"status": "accepted", "task_id": "task-1"})
    monkeypatch.setattr("registry.main.engine.dispatch_async", dispatch_async_mock)

    result = await _perform_dispatch_async("invoice_ocr", {}, machine_id="machine-a")

    assert result["status"] == "error"
    assert result["message"] == "Machine machine-a does not support RPA invoice_ocr"
    dispatch_async_mock.assert_not_awaited()
