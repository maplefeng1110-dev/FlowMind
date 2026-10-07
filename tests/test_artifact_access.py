"""Snapshots are filed under the FlowMind task id and readable only by its owner or an admin."""
import asyncio
import base64
import types

import pytest

AGENT_TOKEN = "agent-token-for-tests"
INTERNAL_TOKEN = "internal-token-for-tests"
ADMIN_PASSWORD = "admin-password-for-tests"
TASK_ID = "6f1c2a7e-0d4b-4c55-9a51-3c1f0b8e2d10"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("FLOWMIND_INTERNAL_API_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("FLOWMIND_ADMIN_PASSWORD", ADMIN_PASSWORD)
    from fastapi.testclient import TestClient

    import registry.db as registry_db
    import registry.main as registry_main

    monkeypatch.setattr(registry_db, "DB_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(registry_main, "ARTIFACTS_DIR", tmp_path / "artifacts")

    async def setup():
        await registry_db.init_db()
        owner = await registry_db.create_user("owner1", "password-1", role="business")
        other = await registry_db.create_user("other1", "password-2", role="business")
        admin = await registry_db.authenticate_user(registry_db.DEFAULT_ADMIN_USERNAME, ADMIN_PASSWORD)
        await registry_db.create_task(TASK_ID, "m1", "rpa_flow", {}, status="error", owner_user_id=owner["id"])
        return {name: await registry_db.create_session(user["id"]) for name, user in
                (("owner", owner), ("other", other), ("admin", admin))}

    sessions = asyncio.run(setup())
    client = TestClient(registry_main.app)
    upload = {"run_id": TASK_ID, "index": 0, "kind": "png", "content_b64": base64.b64encode(b"\x89PNG").decode()}
    assert client.post("/artifacts", json=upload, headers={"X-FlowMind-Agent-Token": AGENT_TOKEN}).status_code == 200
    return client, sessions


def _read(client, session=None):
    headers = {"X-FlowMind-Internal-Token": INTERNAL_TOKEN}
    if session:
        headers["X-FlowMind-Session-Token"] = session
    return client.get(f"/artifacts/{TASK_ID}/step0.png", headers=headers)


def test_owner_reads_snapshot_served_inert(env):
    client, sessions = env
    response = _read(client, sessions["owner"])
    assert response.status_code == 200
    assert response.content == b"\x89PNG"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_other_user_cannot_read_snapshot(env):
    client, sessions = env
    assert _read(client, sessions["other"]).status_code == 404


def test_admin_reads_any_snapshot(env):
    client, sessions = env
    assert _read(client, sessions["admin"]).status_code == 200


def test_user_session_is_required(env):
    client, _sessions = env
    assert _read(client).status_code == 401


def test_executor_exposes_task_id_to_plugins(tmp_path):
    from agent.executor import LocalExecutor
    from agent.task_context import current_task_id

    async def run(**params):
        return {"status": "success", "data": current_task_id()}

    executor = LocalExecutor(tmp_path / "no-plugins")
    executor.plugins["probe"] = types.SimpleNamespace(run=run)
    executor.manifests["probe"] = {"id": "probe"}
    result = asyncio.run(executor.run("probe", {}, task_id=TASK_ID))
    assert result["data"] == TASK_ID
    assert current_task_id() is None
