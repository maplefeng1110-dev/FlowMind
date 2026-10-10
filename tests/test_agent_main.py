import asyncio
from unittest.mock import AsyncMock

import pytest

import agent.main as agent_main


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_heartbeat_messages(monkeypatch):
    sent_payloads = []

    async def fake_send_json(_ws, payload, _send_lock):
        sent_payloads.append(payload)
        if len(sent_payloads) >= 2:
            raise RuntimeError("stop loop")

    monkeypatch.setattr(agent_main, "HEARTBEAT_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(agent_main, "_send_json", fake_send_json)

    await agent_main._heartbeat_loop(object(), asyncio.Lock())

    assert sent_payloads == [{"type": "heartbeat"}, {"type": "heartbeat"}]


@pytest.mark.asyncio
async def test_send_json_serializes_payload_with_lock():
    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send(self, payload):
            self.messages.append(payload)

    ws = FakeWebSocket()
    send_lock = asyncio.Lock()

    await agent_main._send_json(ws, {"type": "result", "task_id": "task-1"}, send_lock)

    assert ws.messages == ['{"type": "result", "task_id": "task-1"}']


def test_build_system_info_includes_agent_admin_metadata(monkeypatch):
    values = {
        "AGENT_ADMIN_ENABLED": "true",
        "AGENT_ADMIN_PUBLIC_URL": "https://agent-a.example.com",
    }

    monkeypatch.setattr(agent_main, "get_agent_env", lambda name, default=None: values.get(name, default))

    system_info = agent_main._build_system_info()

    assert system_info["agent_admin"]["enabled"] is True
    assert system_info["agent_admin"]["api_url"] == "https://agent-a.example.com"


def test_build_register_payload_includes_auth_token(monkeypatch):
    monkeypatch.setattr(agent_main, "MACHINE_ID", "machine-a")
    monkeypatch.setattr(agent_main.executor, "plugins", {"invoice_ocr": object()})
    monkeypatch.setattr(
        agent_main.executor,
        "manifests",
        {"invoice_ocr": {"id": "invoice_ocr", "params_schema": {"type": "object"}}},
    )
    monkeypatch.setattr(agent_main, "_build_system_info", lambda: {"os": "macOS"})
    monkeypatch.setattr(agent_main, "_get_agent_ws_token", lambda: "secret-token")

    payload = agent_main._build_register_payload()

    assert payload["type"] == "register"
    assert payload["machine_id"] == "machine-a"
    assert payload["auth_token"] == "secret-token"
    assert payload["rpas"] == ["invoice_ocr"]
