from unittest.mock import AsyncMock

import pytest

from registry.dispatch import DispatchEngine


class FailingWebSocket:
    async def send_text(self, _payload: str):
        raise RuntimeError("socket closed")


@pytest.mark.asyncio
async def test_dispatch_cleans_up_task_state_when_send_fails(monkeypatch):
    engine = DispatchEngine()
    engine.connections["machine-a"] = FailingWebSocket()

    monkeypatch.setattr("registry.dispatch.create_task", AsyncMock())
    update_task_result = AsyncMock()
    monkeypatch.setattr("registry.dispatch.update_task_result", update_task_result)

    with pytest.raises(RuntimeError, match="Failed to dispatch task"):
        await engine.dispatch("machine-a", "invoice_ocr", {"invoice_path": "a.pdf"})

    assert engine.pending == {}
    assert engine.task_machines == {}
    update_task_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_async_cleans_up_task_state_when_send_fails(monkeypatch):
    engine = DispatchEngine()
    engine.connections["machine-a"] = FailingWebSocket()

    monkeypatch.setattr("registry.dispatch.create_task", AsyncMock())
    update_task_result = AsyncMock()
    monkeypatch.setattr("registry.dispatch.update_task_result", update_task_result)

    with pytest.raises(RuntimeError, match="Failed to dispatch async task"):
        await engine.dispatch_async("machine-a", "send_email", {"to": "demo@example.com"})

    assert engine.task_machines == {}
    update_task_result.assert_awaited_once()
