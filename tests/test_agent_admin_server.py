from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agent.admin_server import app


def _patch_agent_admin_env(monkeypatch, **overrides):
    values = {
        "AGENT_ADMIN_ENABLED": "true",
        "AGENT_ADMIN_TOKEN": "secret-token",
        "AGENT_ADMIN_HOST": "127.0.0.1",
        "AGENT_ADMIN_PORT": "8765",
    }
    values.update(overrides)

    def fake_get_agent_env(name, default=None, **_kwargs):
        return values.get(name, default)

    monkeypatch.setattr("agent.admin_server.get_agent_env", fake_get_agent_env)


def test_agent_admin_scaffold_requires_token(monkeypatch):
    _patch_agent_admin_env(monkeypatch)

    with TestClient(app) as client:
        response = client.post("/api/plugins/scaffold", json={"plugin_id": "weather_api"})

    assert response.status_code == 401


def test_agent_admin_scaffold_creates_plugin(monkeypatch, tmp_path):
    _patch_agent_admin_env(monkeypatch)
    plugin_dir = tmp_path / "weather_api"
    plugin_dir.mkdir(parents=True)
    manifest = {
        "id": "weather_api",
        "description": "天气查询插件",
        "tags": ["weather"],
        "capabilities": ["weather_query"],
        "params_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        "timeout_sec": 45,
    }
    (plugin_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("async def run(**params):\n    return params\n", encoding="utf-8")

    captured = {}

    def fake_scaffold_plugin(argv):
        captured["argv"] = list(argv)
        return plugin_dir

    monkeypatch.setattr("agent.admin_server.scaffold_plugin", fake_scaffold_plugin)

    with TestClient(app) as client:
        response = client.post(
            "/api/plugins/scaffold",
            headers={"X-FlowMind-Agent-Admin-Token": "secret-token"},
            json={
                "plugin_id": "weather_api",
                "description": "天气查询插件",
                "params": ["city:string"],
                "required_params": ["city"],
                "keywords": ["天气查询"],
                "tags": ["weather"],
                "capabilities": ["weather_query"],
                "timeout_sec": 45,
                "force": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["plugin"]["id"] == "weather_api"
    assert body["restart_required"] is True
    assert Path(body["plugin_dir"]).name == "weather_api"
    assert captured["argv"] == [
        "weather_api",
        "--description",
        "天气查询插件",
        "--tag",
        "weather",
        "--capability",
        "weather_query",
        "--keyword",
        "天气查询",
        "--param",
        "city:string",
        "--required",
        "city",
        "--timeout-sec",
        "45",
        "--force",
    ]


def test_agent_admin_scaffold_maps_syntax_error_to_400(monkeypatch):
    _patch_agent_admin_env(monkeypatch)

    def raise_syntax_error(_argv):
        raise SyntaxError("invalid source")

    monkeypatch.setattr("agent.admin_server.scaffold_plugin", raise_syntax_error)

    with TestClient(app) as client:
        response = client.post(
            "/api/plugins/scaffold",
            headers={"X-FlowMind-Agent-Admin-Token": "secret-token"},
            json={"plugin_id": "weather_api"},
        )

    assert response.status_code == 400
    assert "invalid source" in response.json()["detail"]
