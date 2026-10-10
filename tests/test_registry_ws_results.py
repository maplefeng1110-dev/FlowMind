"""Task results over /ws are only accepted from the registered Agent that owns the task."""
import asyncio
import json
import time

import pytest

AGENT_TOKEN = "agent-token-for-tests"


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("FLOWMIND_ADMIN_PASSWORD", "admin-password-for-tests")
    import registry.db as registry_db
    import registry.main as registry_main

    monkeypatch.setattr(registry_db, "DB_PATH", str(tmp_path / "registry.db"))
    asyncio.run(registry_db.init_db())
    return registry_db, registry_main


def _register(ws, machine_id):
    ws.send_text(json.dumps({
        "type": "register", "machine_id": machine_id, "auth_token": AGENT_TOKEN,
        "system_info": {}, "rpas": ["web_query"],
        "manifests": [{"id": "web_query", "params_schema": {"type": "object"}}],
    }))


def _result(task_id, value):
    return json.dumps({"type": "result", "task_id": task_id, "result": {"status": "success", "data": value}})


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _barrier(ws, registry_db):
    """The server handles one socket's frames in order, so once this sentinel
    registration is visible every earlier frame has been processed."""
    _register(ws, "barrier")
    machine_online = lambda: (asyncio.run(registry_db.get_machine("barrier")) or {}).get("status") == "online"  # noqa: E731
    assert _wait_until(machine_online), "registry did not process the socket's frames"


def _task(registry_db, task_id):
    return asyncio.run(registry_db.get_task(task_id))


def test_result_from_unregistered_socket_is_ignored(registry):
    from fastapi.testclient import TestClient

    registry_db, registry_main = registry
    asyncio.run(registry_db.create_task("t-1", "m1", "web_query", {}, status="running"))
    with TestClient(registry_main.app).websocket_connect("/ws") as ws:
        ws.send_text(_result("t-1", "unexpected"))
        _barrier(ws, registry_db)
    task = _task(registry_db, "t-1")
    assert task["status"] == "running"
    assert task.get("result") is None


def test_result_for_another_machines_task_is_ignored(registry):
    from fastapi.testclient import TestClient

    registry_db, registry_main = registry
    asyncio.run(registry_db.create_task("t-2", "m1", "web_query", {}, status="running"))
    with TestClient(registry_main.app).websocket_connect("/ws") as ws:
        _register(ws, "m2")
        ws.send_text(_result("t-2", "unexpected"))
        _barrier(ws, registry_db)
    assert _task(registry_db, "t-2")["status"] == "running"


def test_result_from_owning_machine_is_recorded(registry):
    from fastapi.testclient import TestClient

    registry_db, registry_main = registry
    asyncio.run(registry_db.create_task("t-3", "m1", "web_query", {}, status="running"))
    with TestClient(registry_main.app).websocket_connect("/ws") as ws:
        _register(ws, "m1")
        ws.send_text(_result("t-3", "ok"))
        assert _wait_until(lambda: _task(registry_db, "t-3")["status"] == "success")
    assert _task(registry_db, "t-3")["result"]["data"] == "ok"


def test_in_flight_result_is_accepted_without_a_local_db_record(monkeypatch):
    """Multi-instance without a shared DB: the delivering instance knows the owner in memory."""
    import registry.dispatch as dispatch
    from registry.broker import Broker
    from registry.kv import MemoryKV

    recorded = []

    async def no_task(*args, **kwargs):
        return None

    async def record_result(task_id, status, result=None):
        recorded.append((task_id, status))

    monkeypatch.setattr(dispatch, "get_task", no_task)
    monkeypatch.setattr(dispatch, "update_task_result", record_result)
    engine = dispatch.DispatchEngine(broker=Broker(MemoryKV()))
    engine._task_machines["t-4"] = "m1"

    assert asyncio.run(engine.resolve_from_machine("m2", "t-4", {"status": "success"})) is False
    assert recorded == []
    assert asyncio.run(engine.resolve_from_machine("m1", "t-4", {"status": "success"})) is True
    assert recorded == [("t-4", "success")]
