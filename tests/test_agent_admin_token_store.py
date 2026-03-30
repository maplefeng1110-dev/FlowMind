import json
import os

import pytest

from client.agent_admin_token_store import (
    get_agent_admin_token_store_health,
    get_agent_admin_token_store_path,
    load_agent_admin_token_store,
)


def test_load_agent_admin_token_store_reads_default_and_machine_tokens(monkeypatch, tmp_path):
    store_path = tmp_path / "agent_admin_tokens.json"
    store_path.write_text(
        json.dumps(
            {
                "default_token": "shared-token",
                "machine_tokens": {
                    "machine-a": "token-a",
                    "machine-b": "token-b",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(store_path))

    config = load_agent_admin_token_store()

    assert config["default_token"] == "shared-token"
    assert config["machine_tokens"] == {"machine-a": "token-a", "machine-b": "token-b"}
    assert get_agent_admin_token_store_path() == store_path


def test_load_agent_admin_token_store_supports_rotation_metadata(monkeypatch, tmp_path):
    store_path = tmp_path / "agent_admin_tokens.json"
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-03-29T23:30:00Z",
                "default_token": "shared-token",
                "default_rotated_at": "2026-03-20T00:00:00Z",
                "machine_tokens": {
                    "machine-a": {
                        "token": "token-a",
                        "rotated_at": "2026-03-21T00:00:00Z",
                        "note": "primary",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(store_path))

    config = load_agent_admin_token_store()
    health = get_agent_admin_token_store_health()

    assert config["machine_tokens"]["machine-a"] == "token-a"
    assert config["machine_token_meta"]["machine-a"]["note"] == "primary"
    assert health["machine_tokens"][0]["machine_id"] == "machine-a"
    assert health["machine_tokens"][0]["note"] == "primary"
    assert health["default_token_configured"] is True


def test_load_agent_admin_token_store_returns_empty_config_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(tmp_path / "missing.json"))

    assert load_agent_admin_token_store() == {
        "path": str(tmp_path / "missing.json"),
        "exists": False,
        "schema_version": 1,
        "updated_at": "",
        "default_token": "",
        "default_rotated_at": "",
        "machine_tokens": {},
        "machine_token_meta": {},
        "warnings": [],
    }


def test_load_agent_admin_token_store_rejects_invalid_machine_tokens(monkeypatch, tmp_path):
    store_path = tmp_path / "agent_admin_tokens.json"
    store_path.write_text('{"machine_tokens":["bad"]}', encoding="utf-8")
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(store_path))

    with pytest.raises(ValueError) as exc_info:
        load_agent_admin_token_store()

    assert "machine_tokens" in str(exc_info.value)


def test_agent_admin_token_store_health_warns_for_missing_rotation_metadata(monkeypatch, tmp_path):
    store_path = tmp_path / "agent_admin_tokens.json"
    store_path.write_text(
        json.dumps(
            {
                "default_token": "shared-token",
                "machine_tokens": {
                    "machine-a": "token-a",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(store_path))

    health = get_agent_admin_token_store_health()

    assert any("default_rotated_at" in warning for warning in health["warnings"])
    assert any("machine-a" in warning for warning in health["warnings"])


def test_agent_admin_token_store_health_warns_for_insecure_permissions(monkeypatch, tmp_path):
    if os.name != "posix":
        pytest.skip("permission warning test is POSIX only")

    store_path = tmp_path / "agent_admin_tokens.json"
    store_path.write_text(
        json.dumps(
            {
                "machine_tokens": {
                    "machine-a": {
                        "token": "token-a",
                        "rotated_at": "2026-03-21T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store_path.chmod(0o644)
    monkeypatch.setenv("AGENT_ADMIN_TOKEN_STORE_PATH", str(store_path))

    health = get_agent_admin_token_store_health()

    assert any("chmod 600" in warning for warning in health["warnings"])
