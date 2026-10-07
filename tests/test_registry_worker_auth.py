"""Worker-facing Registry endpoints (AI gateway, artifact upload/read, log ingest)
must reject unauthenticated callers and accept the shared agent token."""
import base64

import pytest

AGENT_TOKEN = "agent-token-for-tests"
INTERNAL_TOKEN = "internal-token-for-tests"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("FLOWMIND_INTERNAL_API_TOKEN", INTERNAL_TOKEN)
    from fastapi.testclient import TestClient

    import registry.db as registry_db
    import registry.main as registry_main

    monkeypatch.setattr(registry_db, "DB_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setattr(registry_main, "ARTIFACTS_DIR", tmp_path / "artifacts")
    return TestClient(registry_main.app)


AGENT = {"X-FlowMind-Agent-Token": AGENT_TOKEN}
INTERNAL = {"X-FlowMind-Internal-Token": INTERNAL_TOKEN}


def _artifact(kind="png", content=b"\x89PNG\r\n", run_id="run-1", index=0):
    return {"run_id": run_id, "index": index, "kind": kind, "content_b64": base64.b64encode(content).decode()}


def test_ai_gateway_requires_worker_credentials(client):
    assert client.get("/ai/providers").status_code == 401
    assert client.get("/ai/providers", headers={"X-FlowMind-Agent-Token": "wrong"}).status_code == 401
    assert client.get("/ai/providers", headers=AGENT).status_code == 200
    assert client.get("/ai/providers", headers=INTERNAL).status_code == 200


def test_log_ingest_requires_worker_credentials(client):
    batch = {"records": [{"level": "WARNING", "source": "m1", "logger": "x", "message": "hi"}]}
    assert client.post("/logs", json=batch).status_code == 401
    response = client.post("/logs", json=batch, headers=AGENT)
    assert response.status_code == 200
    assert response.json() == {"ingested": 1}


def test_artifact_upload_requires_worker_credentials(client, tmp_path):
    assert client.post("/artifacts", json=_artifact()).status_code == 401
    response = client.post("/artifacts", json=_artifact(), headers=AGENT)
    assert response.status_code == 200
    assert response.json() == {"url": "/artifacts/run-1/step0.png"}
    assert (tmp_path / "artifacts" / "run-1" / "step0.png").read_bytes() == b"\x89PNG\r\n"


def test_artifact_upload_rejects_unknown_kind_and_bad_payload(client):
    assert client.post("/artifacts", json=_artifact(kind="js"), headers=AGENT).status_code == 400
    bad = {**_artifact(), "content_b64": "not base64!!"}
    assert client.post("/artifacts", json=bad, headers=AGENT).status_code == 400


def test_artifact_read_is_internal_only_and_served_inert(client):
    client.post("/artifacts", json=_artifact(kind="html", content=b"<p>snapshot</p>"), headers=AGENT)
    assert client.get("/artifacts/run-1/step0.html").status_code == 401
    assert client.get("/artifacts/run-1/step0.html", headers=AGENT).status_code == 401
    response = client.get("/artifacts/run-1/step0.html", headers=INTERNAL)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/artifacts/run-1/other.txt", headers=INTERNAL).status_code == 404


def test_artifact_routes_are_registered_once():
    import registry.main as registry_main

    paths = [(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", None) or ()))) for r in registry_main.app.routes]
    assert paths.count(("/artifacts", ("POST",))) == 1
    assert not any(getattr(r, "path", "") == "/artifacts" and r.__class__.__name__ == "Mount" for r in registry_main.app.routes)
