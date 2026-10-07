"""Per-machine agent tokens (FLOWMIND_AGENT_TOKENS_JSON) alongside the shared token."""
import asyncio
import json

import pytest

from utils.internal_api import get_agent_token_map, validate_agent_registration, validate_agent_token

MACHINE_TOKENS = {"machine-a": "token-a", "machine-b": "token-b"}


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setenv("FLOWMIND_AGENT_TOKENS_JSON", json.dumps(MACHINE_TOKENS))
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", "shared-token")


def test_listed_machine_must_use_its_own_token(tokens):
    assert validate_agent_registration("machine-a", "token-a") is True
    assert validate_agent_registration("machine-a", "shared-token") is False
    assert validate_agent_registration("machine-a", "token-b") is False


def test_unlisted_machine_uses_the_shared_token(tokens):
    assert validate_agent_registration("machine-z", "shared-token") is True
    assert validate_agent_registration("machine-z", "token-a") is False


def test_without_a_shared_token_only_listed_machines_register(tokens, monkeypatch):
    monkeypatch.delenv("FLOWMIND_AGENT_WS_TOKEN")
    assert validate_agent_registration("machine-z", "") is False
    assert validate_agent_registration("machine-b", "token-b") is True


def test_worker_endpoints_accept_any_configured_agent_token(tokens):
    assert all(validate_agent_token(t) for t in ("shared-token", "token-a", "token-b"))
    assert validate_agent_token("other") is False
    assert validate_agent_token("") is False


def test_malformed_token_map_fails_closed(monkeypatch):
    monkeypatch.setenv("FLOWMIND_AGENT_TOKENS_JSON", "[1, 2]")
    with pytest.raises(ValueError):
        get_agent_token_map()


def test_registry_rejects_shared_token_for_a_listed_machine(tokens, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import registry.db as registry_db
    import registry.main as registry_main

    monkeypatch.setenv("FLOWMIND_ADMIN_PASSWORD", "admin-password-for-tests")
    monkeypatch.setattr(registry_db, "DB_PATH", str(tmp_path / "registry.db"))
    asyncio.run(registry_db.init_db())

    with TestClient(registry_main.app).websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "register", "machine_id": "machine-a", "auth_token": "shared-token"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008
    assert asyncio.run(registry_db.get_machine("machine-a")) is None


def test_log_handler_falls_back_to_internal_token(monkeypatch):
    import utils.logger as logger_mod

    sent = []
    monkeypatch.setattr(logger_mod, "_env_cfg", lambda name, default=None: {"SECRET_KEY": "internal"}.get(name, default))
    monkeypatch.setattr(logger_mod.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req))
    logger_mod._CentralLogHandler("http://127.0.0.1:1", "registry")._post([{"message": "x"}])
    assert sent[0].get_header("X-flowmind-internal-token") == "internal"
    assert sent[0].get_header("X-flowmind-agent-token") is None
