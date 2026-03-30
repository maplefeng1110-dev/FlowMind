import asyncio
import json

import pytest

from registry import db as registry_db
from registry.dispatch import DispatchEngine
from registry.main import _perform_dispatch


class RecordingWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload: str):
        self.messages.append(json.loads(payload))


@pytest.mark.asyncio
async def test_async_task_lifecycle_persists_conversation_context(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    engine = DispatchEngine()
    ws = RecordingWebSocket()

    await engine.register(
        "machine-a",
        ws,
        system_info={"os": "macOS"},
        rpas=["invoice_ocr"],
        manifests=[{"id": "invoice_ocr", "description": "OCR", "params_schema": {"type": "object"}}],
    )

    accepted = await engine.dispatch_async(
        "machine-a",
        "invoice_ocr",
        {"invoice_path": "invoice.png"},
        conversation_id="conv-1",
        conversation_title="测试对话",
    )

    task_id = accepted["task_id"]
    task = await registry_db.get_task(task_id)
    assert task["status"] == "running"
    assert task["conversation_id"] == "conv-1"
    assert task["conversation_title"] == "测试对话"
    assert ws.messages[0]["rpa_id"] == "invoice_ocr"

    await engine.resolve(task_id, {"status": "success", "data": {"text": "ok"}})

    finished = await registry_db.get_task(task_id)
    assert finished["status"] == "success"
    assert finished["result"]["data"]["text"] == "ok"


@pytest.mark.asyncio
async def test_disconnect_marks_running_sync_task_as_error(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    engine = DispatchEngine()
    ws = RecordingWebSocket()
    await engine.register(
        "machine-a",
        ws,
        system_info={},
        rpas=["invoice_ocr"],
        manifests=[{"id": "invoice_ocr", "description": "OCR", "params_schema": {"type": "object"}}],
    )

    dispatch_task = asyncio.create_task(
        engine.dispatch("machine-a", "invoice_ocr", {"invoice_path": "invoice.png"}, timeout=5)
    )
    for _ in range(100):
        if ws.messages or dispatch_task.done():
            break
        await asyncio.sleep(0.01)

    if dispatch_task.done() and not ws.messages:
        await dispatch_task

    sent_task_id = ws.messages[0]["task_id"]
    await engine.disconnect("machine-a")

    with pytest.raises(Exception, match="disconnected"):
        await dispatch_task

    task = await registry_db.get_task(sent_task_id)
    assert task["status"] == "error"
    assert task["result"]["error"] == "Machine disconnected"


@pytest.mark.asyncio
async def test_re_register_after_disconnect_allows_dispatch_again(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    engine = DispatchEngine()
    first_ws = RecordingWebSocket()
    second_ws = RecordingWebSocket()

    await engine.register("machine-a", first_ws, {}, ["web_query"], [{"id": "web_query", "params_schema": {"type": "object"}}])
    await engine.disconnect("machine-a")
    await engine.register("machine-a", second_ws, {}, ["web_query"], [{"id": "web_query", "params_schema": {"type": "object"}}])

    accepted = await engine.dispatch_async("machine-a", "web_query", {"query": "天气"})

    assert accepted["status"] == "accepted"
    assert len(first_ws.messages) == 0
    assert second_ws.messages[0]["params"]["query"] == "天气"


@pytest.mark.asyncio
async def test_perform_dispatch_routes_to_one_of_multiple_online_machines(monkeypatch, tmp_path):
    test_db = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(test_db))

    await registry_db.init_db()
    engine = DispatchEngine()
    ws_a = RecordingWebSocket()
    ws_b = RecordingWebSocket()

    await engine.register("machine-a", ws_a, {}, ["send_email"], [{"id": "send_email", "params_schema": {"type": "object"}}])
    await engine.register("machine-b", ws_b, {}, ["send_email"], [{"id": "send_email", "params_schema": {"type": "object"}}])

    monkeypatch.setattr("registry.main.engine", engine)

    async def resolve_later():
        while not ws_a.messages and not ws_b.messages:
            await asyncio.sleep(0)
        sent = ws_a.messages[0] if ws_a.messages else ws_b.messages[0]
        await engine.resolve(sent["task_id"], {"status": "success", "data": {"sent": True}})

    resolver = asyncio.create_task(resolve_later())
    result = await _perform_dispatch("send_email", {"to": "demo@example.com"})
    await resolver

    assert result["status"] == "success"
    assert result["data"]["sent"] is True
    assert (len(ws_a.messages), len(ws_b.messages)) in {(1, 0), (0, 1)}
