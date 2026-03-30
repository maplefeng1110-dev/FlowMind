from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

import client.web_server as web_server
import client.web_server_tooling as web_server_tooling
from client.web_auth import AuthUser
from client.web_server import (
    ChatRequest,
    app,
    chat_stream,
    chat_with_mcp,
)
from client.web_server_logic import _build_augmented_tool_description, _collect_async_tool_ids, _get_required_rpas_for_messages


class DummySSEClient:
    async def __aenter__(self):
        return ("reader", "writer")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummySession:
    def __init__(self, *_args):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return type("ToolsResponse", (), {"tools": []})()

    async def call_tool(self, name, args):
        if name == "list_rpas":
            return type(
                "ToolResult",
                (),
                {
                    "content": [
                        type(
                            "TextContent",
                            (),
                            {
                                "text": '[{"id":"excel_processor","description":"Excel 工具","tags":["excel"],"online_machine_ids":["machine-a"],"online_machine_count":1}]'
                            },
                        )()
                    ]
                },
            )()
        return type(
            "ToolResult",
            (),
            {"content": [type("TextContent", (), {"text": f"{name}:{args}"})()]},
        )()


class CountingSession(DummySession):
    call_count = 0

    async def call_tool(self, name, args):
        if name == "list_rpas":
            return await super().call_tool(name, args)
        CountingSession.call_count += 1
        return type(
            "ToolResult",
            (),
            {"content": [type("TextContent", (), {"text": f"{name}:{args}"})()]},
        )()


def _auth_user(role="business", assigned_rpa_ids=None):
    return AuthUser(
        id="user-1",
        username="demo",
        role=role,
        display_name="演示账号",
        is_active=True,
        assigned_rpa_ids=list(assigned_rpa_ids or []),
    )


def _patch_authenticated_user(monkeypatch, role="business", assigned_rpa_ids=None):
    user = _auth_user(role=role, assigned_rpa_ids=assigned_rpa_ids)

    async def _current_user(*_args, **_kwargs):
        return user

    async def _admin_user(*_args, **_kwargs):
        if role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员账号可执行此操作")
        return user

    monkeypatch.setattr("client.web_server.require_current_user", _current_user)
    monkeypatch.setattr("client.web_server.require_admin_user", _admin_user)
    return user


@pytest.mark.asyncio
async def test_chat_stream_returns_error_when_no_provider(monkeypatch):
    monkeypatch.setattr("client.web_server.ai_manager.get_preferred_provider", lambda: None)

    result = await chat_stream(ChatRequest(messages=[{"role": "user", "content": "hi"}]))

    assert result == {"response": "Error: No AI provider configured"}


@pytest.mark.asyncio
async def test_chat_stream_uses_single_model_call_for_final_answer(monkeypatch):
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    ai_chat = AsyncMock(return_value=("final answer", []))
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)

    async def forbidden_stream(*_args, **_kwargs):
        raise AssertionError("stream_chat should not be called for the final answer")
        yield ""

    monkeypatch.setattr("client.web_server_chat.ai_manager.stream_chat", forbidden_stream)

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "hello"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert "final answer" in joined
    assert "[DONE]" in joined
    ai_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_with_mcp_handles_tool_call(monkeypatch):
    tool_call = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "invoice_ocr", "arguments": '{"invoice_path":"a.pdf"}'})(),
        },
    )()

    ai_chat = AsyncMock(side_effect=[("calling tool", [tool_call]), ("done", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)

    result = await chat_with_mcp([{"role": "user", "content": "run tool"}], "deepseek")

    assert result["response"] == "done"
    assert ai_chat.await_count == 2


@pytest.mark.asyncio
async def test_chat_with_mcp_deduplicates_identical_tool_calls(monkeypatch):
    tool_call_1 = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "word_processor", "arguments": '{"operation":"summarize","document_path":"a.docx"}'})(),
        },
    )()
    tool_call_2 = type(
        "ToolCall",
        (),
        {
            "id": "tool-2",
            "function": type("Function", (), {"name": "word_processor", "arguments": '{"operation":"summarize","document_path":"a.docx"}'})(),
        },
    )()

    CountingSession.call_count = 0
    ai_chat = AsyncMock(side_effect=[("first", [tool_call_1]), ("second", [tool_call_2]), ("done", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", CountingSession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)

    result = await chat_with_mcp([{"role": "user", "content": "run tool"}], "deepseek")

    assert result["response"] == "done"
    assert CountingSession.call_count == 1


@pytest.mark.asyncio
async def test_chat_stream_emits_tool_trace_events(monkeypatch):
    tool_call = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "web_query", "arguments": '{"query":"天气"}'})(),
        },
    )()

    ai_chat = AsyncMock(side_effect=[("calling tool", [tool_call]), ("final answer", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "查天气"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert '"type": "tool_start"' in joined
    assert '"type": "tool_result"' in joined
    assert "final answer" in joined


@pytest.mark.asyncio
async def test_chat_stream_marks_duplicate_tool_calls_as_cached(monkeypatch):
    tool_call_1 = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "word_processor", "arguments": '{"operation":"summarize","document_path":"a.docx"}'})(),
        },
    )()
    tool_call_2 = type(
        "ToolCall",
        (),
        {
            "id": "tool-2",
            "function": type("Function", (), {"name": "word_processor", "arguments": '{"operation":"summarize","document_path":"a.docx"}'})(),
        },
    )()

    CountingSession.call_count = 0
    ai_chat = AsyncMock(side_effect=[("first", [tool_call_1]), ("second", [tool_call_2]), ("final answer", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", CountingSession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "查文档"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert '"status": "cached"' in joined
    assert CountingSession.call_count == 1


@pytest.mark.asyncio
async def test_chat_stream_marks_long_running_tools_as_pending(monkeypatch):
    tool_call = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "invoice_ocr", "arguments": '{"invoice_path":"a.pdf"}'})(),
        },
    )()

    ai_chat = AsyncMock(side_effect=[("calling tool", [tool_call]), ("后台执行中", [])])
    dispatch_async = AsyncMock(return_value={"status": "accepted", "task_id": "task-123"})
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)
    monkeypatch.setattr(
        "client.web_server_chat._fetch_registered_rpas_from_session",
        AsyncMock(return_value=[{"id": "invoice_ocr", "timeout_sec": 60}]),
    )
    monkeypatch.setattr("client.web_server_tooling._dispatch_rpa_async", dispatch_async)

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "识别发票"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert '"status": "pending"' in joined
    assert '"task_id": "task-123"' in joined
    dispatch_async.assert_awaited_once()


def test_api_tools_returns_dynamic_registered_plugins(monkeypatch):
    _patch_authenticated_user(monkeypatch, assigned_rpa_ids=["excel_processor"])
    monkeypatch.setattr(
        "client.web_server.get_available_rpas",
        AsyncMock(
            return_value=[
                {
                    "id": "excel_processor",
                    "description": "Excel 工具",
                    "online_machine_ids": ["machine-a"],
                    "online_machine_count": 1,
                },
                {
                    "id": "invoice_ocr",
                    "description": "OCR 工具",
                    "online_machine_ids": ["machine-b"],
                    "online_machine_count": 1,
                },
            ]
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["tools"][0]["id"] == "excel_processor"
    assert body["tools"][0]["online_machine_ids"] == ["machine-a"]
    assert body["tools"][0]["online_machine_count"] == 1
    assert len(body["tools"]) == 1


def test_api_task_proxies_registry_response(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value={"id": "task-123", "status": "running", "owner_user_id": "user-1"})
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/task/task-123")

    assert response.status_code == 200
    assert response.json()["id"] == "task-123"
    assert proxy_json.await_args.args == ("GET", "/task/task-123")
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_tasks_proxies_registry_list(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value=[{"id": "task-1", "rpa_id": "invoice_ocr", "status": "running"}])
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/tasks?limit=20")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "task-1"
    assert proxy_json.await_args.args == ("GET", "/tasks")
    assert proxy_json.await_args.kwargs["params"] == {"limit": 20}
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_delete_task_proxies_registry_delete(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value={"status": "success", "deleted_task_id": "task-1"})
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.delete("/api/task/task-1")

    assert response.status_code == 200
    assert response.json()["deleted_task_id"] == "task-1"
    assert proxy_json.await_args.args == ("DELETE", "/task/task-1")
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_machines_proxies_registry_list_for_business_user(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="business", assigned_rpa_ids=["invoice_ocr"])
    proxy_json = AsyncMock(
        return_value=[
            {
                "id": "machine-a",
                "status": "online",
                "rpas": ["invoice_ocr"],
                "running_task_count": 1,
            }
        ]
    )
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "biz-session")

    with TestClient(app) as client:
        response = client.get("/api/machines")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "machine-a"
    assert proxy_json.await_args.args == ("GET", "/machines")
    assert proxy_json.await_args.kwargs["session_token"] == "biz-session"


def test_api_machines_proxies_registry_list_for_admin(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")
    proxy_json = AsyncMock(
        return_value=[
            {
                "id": "machine-a",
                "status": "online",
                "rpas": ["invoice_ocr", "send_email"],
                "running_task_count": 1,
            }
        ]
    )
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "admin-session")

    with TestClient(app) as client:
        response = client.get("/api/machines")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "machine-a"
    assert proxy_json.await_args.args == ("GET", "/machines")
    assert proxy_json.await_args.kwargs["session_token"] == "admin-session"


def test_api_delete_machine_requires_admin_user(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="business")

    with TestClient(app) as client:
        response = client.delete("/api/machines/machine-a")

    assert response.status_code == 403


def test_api_delete_machine_proxies_registry_delete_for_admin(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")
    proxy_json = AsyncMock(return_value={"status": "success", "deleted_machine_id": "machine-a"})
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "admin-session")

    with TestClient(app) as client:
        response = client.delete("/api/machines/machine-a")

    assert response.status_code == 200
    assert response.json()["deleted_machine_id"] == "machine-a"
    assert proxy_json.await_args.args == ("DELETE", "/machines/machine-a")
    assert proxy_json.await_args.kwargs["session_token"] == "admin-session"


def test_admin_scaffold_plugin_requires_admin(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="business")

    with TestClient(app) as client:
        response = client.post("/api/admin/plugins/scaffold", json={"plugin_id": "weather_api"})

    assert response.status_code == 403


def test_admin_scaffold_plugin_creates_plugin_and_registers_manifest(monkeypatch, tmp_path):
    _patch_authenticated_user(monkeypatch, role="admin")
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

    upsert_rpa_manifests = AsyncMock()
    monkeypatch.setattr("client.web_server._load_local_agent_scaffold_plugin", lambda: fake_scaffold_plugin)
    monkeypatch.setattr("client.web_server.upsert_rpa_manifests", upsert_rpa_manifests)

    with TestClient(app) as client:
        response = client.post(
            "/api/admin/plugins/scaffold",
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
    assert "weather_api" in body["plugin_dir"]
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
    upsert_rpa_manifests.assert_awaited_once()
    assert upsert_rpa_manifests.await_args.args[0] == [manifest]


def test_admin_scaffold_plugin_returns_503_when_local_agent_scaffold_is_unavailable(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")

    def raise_unavailable():
        raise HTTPException(status_code=503, detail="当前部署未提供本地 Agent 脚手架能力。")

    monkeypatch.setattr("client.web_server._load_local_agent_scaffold_plugin", raise_unavailable)

    with TestClient(app) as client:
        response = client.post("/api/admin/plugins/scaffold", json={"plugin_id": "weather_api"})

    assert response.status_code == 503


def test_admin_scaffold_plugin_maps_local_syntax_error_to_400(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")

    def raise_syntax_error():
        def _runner(_argv):
            raise SyntaxError("invalid source")

        return _runner

    monkeypatch.setattr("client.web_server._load_local_agent_scaffold_plugin", raise_syntax_error)

    with TestClient(app) as client:
        response = client.post("/api/admin/plugins/scaffold", json={"plugin_id": "weather_api"})

    assert response.status_code == 400
    assert "invalid source" in response.json()["detail"]


def test_admin_scaffold_plugin_uses_remote_agent_api_when_configured(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")
    manifest = {
        "id": "weather_api",
        "description": "天气查询插件",
        "tags": ["weather"],
        "capabilities": ["weather_query"],
        "params_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        "timeout_sec": 45,
    }
    result = {
        "plugin": manifest,
        "plugin_dir": "/srv/agent/plugins/weather_api",
        "files": ["manifest.yaml", "__init__.py"],
        "restart_required": True,
    }

    async def fake_remote_agent_call(data, base_url):
        assert base_url == "http://agent-admin:8765"
        return result

    upsert_rpa_manifests = AsyncMock()
    monkeypatch.setattr("client.web_server._resolve_scaffold_target_agent_api_url", AsyncMock(return_value="http://agent-admin:8765"))
    monkeypatch.setattr("client.web_server._scaffold_plugin_via_remote_agent_api", fake_remote_agent_call)
    monkeypatch.setattr("client.web_server.upsert_rpa_manifests", upsert_rpa_manifests)
    monkeypatch.setattr(
        "client.web_server._load_local_agent_scaffold_plugin",
        lambda: (_ for _ in ()).throw(AssertionError("should not use local scaffold")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/admin/plugins/scaffold",
            json={
                "plugin_id": "weather_api",
                "description": "天气查询插件",
                "tags": ["weather"],
                "capabilities": ["weather_query"],
                "timeout_sec": 45,
            },
        )

    assert response.status_code == 200
    assert response.json()["plugin_dir"] == "/srv/agent/plugins/weather_api"
    upsert_rpa_manifests.assert_awaited_once_with([manifest])


def test_admin_scaffold_plugin_uses_selected_machine_admin_api(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")
    manifest = {
        "id": "weather_api",
        "description": "天气查询插件",
        "tags": ["weather"],
        "capabilities": ["weather_query"],
        "params_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        "timeout_sec": 45,
    }
    result = {
        "plugin": manifest,
        "plugin_dir": "/srv/agent-a/plugins/weather_api",
        "files": ["manifest.yaml", "__init__.py"],
        "restart_required": True,
    }
    proxy_json = AsyncMock(
        return_value=[
            {
                "id": "machine-a",
                "status": "online",
                "system_info": {
                    "agent_admin": {"enabled": True, "api_url": "https://agent-a.example.com"}
                },
            }
        ]
    )
    captured = {}

    async def fake_remote_agent_call(data, base_url):
        captured["machine_id"] = data.machine_id
        captured["base_url"] = base_url
        return result

    upsert_rpa_manifests = AsyncMock()
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "admin-session")
    monkeypatch.setattr("client.web_server._get_agent_admin_api_url", lambda: "")
    monkeypatch.setattr("client.web_server._scaffold_plugin_via_remote_agent_api", fake_remote_agent_call)
    monkeypatch.setattr("client.web_server.upsert_rpa_manifests", upsert_rpa_manifests)

    with TestClient(app) as client:
        response = client.post(
            "/api/admin/plugins/scaffold",
            json={
                "plugin_id": "weather_api",
                "machine_id": "machine-a",
                "description": "天气查询插件",
                "timeout_sec": 45,
            },
        )

    assert response.status_code == 200
    assert captured == {"machine_id": "machine-a", "base_url": "https://agent-a.example.com"}
    upsert_rpa_manifests.assert_awaited_once_with([manifest])


@pytest.mark.asyncio
async def test_scaffold_plugin_via_remote_agent_api_uses_machine_specific_token(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "plugin": {"id": "weather_api"},
                "plugin_dir": "/srv/agent-a/plugins/weather_api",
                "files": ["manifest.yaml", "__init__.py"],
                "restart_required": True,
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers or {}
            return FakeResponse()

    monkeypatch.setattr("client.web_server.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        "client.web_server._load_agent_admin_token_store_config",
        lambda: {"default_token": "", "machine_tokens": {"machine-a": "token-a"}},
    )
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token_map", lambda: {"machine-a": "token-a"})
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token", lambda: "shared-token")

    result = await web_server._scaffold_plugin_via_remote_agent_api(
        web_server.ScaffoldPluginRequest(plugin_id="weather_api", machine_id="machine-a"),
        "https://agent-a.example.com",
    )

    assert result["plugin"]["id"] == "weather_api"
    assert captured["url"] == "https://agent-a.example.com/api/plugins/scaffold"
    assert captured["headers"]["X-FlowMind-Agent-Admin-Token"] == "token-a"
    assert "machine_id" not in captured["json"]


def test_resolve_agent_admin_api_token_falls_back_to_shared_token(monkeypatch):
    monkeypatch.setattr(
        "client.web_server._load_agent_admin_token_store_config",
        lambda: {"default_token": "", "machine_tokens": {"machine-a": "token-a"}},
    )
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token_map", lambda: {"machine-a": "token-a"})
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token", lambda: "shared-token")

    assert web_server._resolve_agent_admin_api_token("machine-b") == "shared-token"


def test_resolve_agent_admin_api_token_prefers_store_default_token(monkeypatch):
    monkeypatch.setattr(
        "client.web_server._load_agent_admin_token_store_config",
        lambda: {"default_token": "store-default", "machine_tokens": {}},
    )
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token_map", lambda: {})
    monkeypatch.setattr("client.web_server._get_agent_admin_api_token", lambda: "shared-token")

    assert web_server._resolve_agent_admin_api_token("machine-b") == "store-default"


def test_get_agent_admin_api_token_map_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "client.web_server._load_agent_admin_token_store_config",
        lambda: {"default_token": "", "machine_tokens": {}},
    )
    monkeypatch.setattr("client.web_server._get_agent_admin_api_tokens_json", lambda: "{not-json}")

    with pytest.raises(HTTPException) as exc_info:
        web_server._get_agent_admin_api_token_map()

    assert exc_info.value.status_code == 500
    assert "AGENT_ADMIN_API_TOKENS_JSON" in exc_info.value.detail


def test_get_agent_admin_api_token_map_prefers_token_store_over_env(monkeypatch):
    monkeypatch.setattr(
        "client.web_server._load_agent_admin_token_store_config",
        lambda: {"default_token": "", "machine_tokens": {"machine-a": "store-token"}},
    )
    monkeypatch.setattr(
        "client.web_server._get_agent_admin_api_tokens_json",
        lambda: '{"machine-a":"env-token","machine-b":"env-token-b"}',
    )

    assert web_server._get_agent_admin_api_token_map() == {
        "machine-a": "store-token",
        "machine-b": "env-token-b",
    }


def test_admin_get_agent_admin_token_store_requires_admin(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="business")

    with TestClient(app) as client:
        response = client.get("/api/admin/agent-admin-token-store")

    assert response.status_code == 403


def test_admin_get_agent_admin_token_store_returns_sanitized_health(monkeypatch):
    _patch_authenticated_user(monkeypatch, role="admin")
    monkeypatch.setattr(
        "client.web_server.get_agent_admin_token_store_health",
        lambda: {
            "path": "data/agent_admin_tokens.json",
            "exists": True,
            "schema_version": 1,
            "updated_at": "2026-03-29T23:30:00Z",
            "warnings": ["permission warning"],
            "default_token_configured": True,
            "default_rotated_at": "2026-03-20T00:00:00Z",
            "default_token_stale": False,
            "machine_tokens": [
                {
                    "machine_id": "machine-a",
                    "rotated_at": "2026-03-21T00:00:00Z",
                    "note": "primary",
                    "stale": False,
                }
            ],
        },
    )

    with TestClient(app) as client:
        response = client.get("/api/admin/agent-admin-token-store")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "data/agent_admin_tokens.json"
    assert body["machine_tokens"][0]["machine_id"] == "machine-a"
    assert "token" not in body["machine_tokens"][0]


def test_upload_file_returns_500_when_storage_fails(monkeypatch):
    _patch_authenticated_user(monkeypatch)

    def raise_storage_error(_self, _content):
        raise OSError("disk full")

    monkeypatch.setattr("client.web_server.Path.write_bytes", raise_storage_error)

    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save uploaded file"


def test_api_history_conversations_proxies_registry_response(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value=[{"id": "conv-1", "title": "测试对话"}])
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/history/conversations")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "conv-1"
    assert proxy_json.await_args.args == ("GET", "/history/conversations")
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_common_tasks_proxies_registry_response(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value=[{"id": "task-1", "title": "发票处理"}])
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/common-tasks")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "task-1"
    assert proxy_json.await_args.args == ("GET", "/common-tasks")
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_create_common_task_proxies_payload(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value={"id": "task-1", "title": "发票处理"})
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.post(
            "/api/common-tasks",
            json={
                "title": "发票处理",
                "prompt": "把这些发票识别后汇总到 Excel，再发邮件给财务。",
                "summary": "invoice_ocr -> excel_processor -> send_email",
                "plan": {"status": "completed"},
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == "task-1"
    assert proxy_json.await_args.args == ("POST", "/common-tasks")
    assert proxy_json.await_args.kwargs["json_body"]["title"] == "发票处理"
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_schedules_proxy_registry_response(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value=[{"id": "schedule-1", "title": "财务日报"}])
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/schedules")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "schedule-1"
    assert proxy_json.await_args.args == ("GET", "/schedules")
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_create_schedule_proxies_payload(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value={"id": "schedule-1", "title": "财务日报"})
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.post(
            "/api/schedules",
            json={
                "title": "财务日报",
                "prompt": "整理日报并发给财务",
                "schedule_type": "daily",
                "schedule_config": {"time": "09:00"},
                "timezone": "Asia/Shanghai",
                "is_active": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == "schedule-1"
    assert proxy_json.await_args.args == ("POST", "/schedules")
    assert proxy_json.await_args.kwargs["json_body"]["schedule_type"] == "daily"
    assert proxy_json.await_args.kwargs["session_token"] == "session-1"


def test_api_schedule_runs_proxy_registry_response(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    proxy_json = AsyncMock(return_value=[{"id": "run-1", "status": "success"}])
    monkeypatch.setattr("client.web_server._proxy_json", proxy_json)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.get("/api/schedule-runs?limit=20")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "run-1"
    assert proxy_json.await_args.args == ("GET", "/schedule-runs")
    assert proxy_json.await_args.kwargs["params"] == {"limit": 20}


def test_api_export_download_returns_csv_attachment(monkeypatch):
    _patch_authenticated_user(monkeypatch)

    async def fake_proxy_request(_request, method, path, *, json_body=None, params=None):
        assert method == "GET"
        assert path == "/tasks"
        assert params == {"limit": 200}
        return [
            {
                "id": "task-1",
                "status": "success",
                "rpa_id": "invoice_ocr",
                "machine_id": "machine-a",
                "conversation_id": "conv-1",
                "conversation_title": "测试对话",
                "created_at": "2026-03-30 10:00:00",
                "updated_at": "2026-03-30 10:01:00",
                "result": {"status": "success", "data": {"text": "ok"}},
            }
        ]

    monkeypatch.setattr("client.web_server._proxy_registry_request", fake_proxy_request)

    with TestClient(app) as client:
        response = client.post("/api/exports/download", json={"resource": "tasks", "format": "csv"})

    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    assert "task-1" in response.text
    assert "conversation_title" in response.text


def test_api_task_summary_uses_ai_when_available(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    monkeypatch.setattr("client.web_server_tasks.ai_manager.get_preferred_provider", lambda: "deepseek")
    monkeypatch.setattr(
        "client.web_server_tasks.ai_manager.simple_chat",
        AsyncMock(return_value="AI 总结：发票已识别完成。"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/task-summary",
            json={
                "tool_name": "invoice_ocr",
                "arguments": {"invoice_path": "a.pdf"},
                "task": {
                    "status": "success",
                    "result": {"status": "success", "data": {"text": "invoice text"}},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["summary"] == "AI 总结：发票已识别完成。"


def test_api_task_summary_falls_back_without_provider(monkeypatch):
    _patch_authenticated_user(monkeypatch)
    monkeypatch.setattr("client.web_server_tasks.ai_manager.get_preferred_provider", lambda: None)

    with TestClient(app) as client:
        response = client.post(
            "/api/task-summary",
            json={
                "tool_name": "invoice_ocr",
                "arguments": {"invoice_path": "a.pdf"},
                "task": {
                    "status": "timeout",
                    "result": {"status": "timeout", "message": "Task timed out"},
                },
            },
        )

    assert response.status_code == 200
    assert "已超时" in response.json()["summary"]


def test_api_chat_passes_conversation_context_to_non_stream_handler(monkeypatch):
    _patch_authenticated_user(monkeypatch, assigned_rpa_ids=["invoice_ocr"])
    handler = AsyncMock(return_value={"response": "ok", "provider": "deepseek"})
    monkeypatch.setattr("client.web_server.chat_with_mcp", handler)
    monkeypatch.setattr("client.web_server.get_session_token_from_request", lambda _request: "session-1")

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "provider": "deepseek",
                "conversation_id": "conv-1",
                "conversation_title": "测试对话",
                "messages": [{"role": "user", "content": "帮我执行 OCR"}],
            },
        )

    assert response.status_code == 200
    kwargs = handler.await_args.kwargs
    assert kwargs["conversation_context"] == {
        "conversation_id": "conv-1",
        "conversation_title": "测试对话",
    }
    assert kwargs["allowed_rpa_ids"] == {"invoice_ocr"}
    assert kwargs["session_token"] == "session-1"


def test_collect_async_tool_ids_includes_all_registered_rpas():
    async_tool_ids = _collect_async_tool_ids(
        [
            {"id": "send_email", "timeout_sec": 30},
            {"id": "invoice_ocr", "timeout_sec": 60},
            {"description": "missing id"},
        ]
    )

    assert "send_email" in async_tool_ids
    assert "invoice_ocr" in async_tool_ids
    assert len(async_tool_ids) == 2


def test_build_augmented_tool_description_includes_enterprise_capability_map():
    description = _build_augmented_tool_description(
        {"function": {"description": "发送外部邮件通知"}},
        {
            "id": "send_email",
            "description": "邮件发送",
            "tool_profile": {
                "solves": ["把结果通知给指定收件人"],
                "suitable_inputs": ["收件人、主题、正文都已准备好"],
                "outputs": ["返回投递状态"],
                "prerequisites": ["SMTP 配置完成"],
                "common_failure_reasons": ["地址非法"],
                "supports_batch": False,
                "has_side_effects": True,
                "requires_confirmation": True,
                "confirmation_hint": "执行前需要用户明确确认。",
            },
        },
    )

    assert "解决问题" in description
    assert "前置条件" in description
    assert "确认要求" in description


def test_evaluate_tool_constraints_blocks_missing_params_and_confirmation():
    result = web_server_tooling._evaluate_tool_constraints(
        "send_email",
        {"to": "demo@example.com"},
        {
            "id": "send_email",
            "params_schema": {
                "type": "object",
                "required": ["to", "subject", "body"],
                "properties": {},
            },
            "tool_profile": {
                "requires_confirmation": True,
                "has_side_effects": True,
            },
        },
        [{"role": "user", "content": "帮我整理结果"}],
        set(),
        0,
    )

    codes = {item["code"] for item in result["constraints"]}
    assert result["allowed"] is False
    assert "missing_required_params" in codes
    assert "confirmation_required" in codes


def test_get_required_rpas_for_messages_uses_manifest_enforcement():
    required = _get_required_rpas_for_messages(
        [{"role": "user", "content": "请帮我识别发票并发送邮件"}],
        [
            {
                "id": "invoice_ocr",
                "enforcement": {
                    "must_call_when_matched": True,
                    "intent_keywords": ["识别发票"],
                },
            },
            {
                "id": "send_email",
                "enforcement": {
                    "must_call_when_matched": True,
                    "intent_keywords": ["发送邮件"],
                },
            },
            {
                "id": "web_query",
                "enforcement": {
                    "must_call_when_matched": True,
                    "intent_keywords": ["联网查询"],
                },
            },
        ],
    )

    assert [item["id"] for item in required] == ["invoice_ocr", "send_email"]


@pytest.mark.asyncio
async def test_chat_stream_emits_runtime_plan_and_reusable_task_suggestion(monkeypatch):
    tool_call = type(
        "ToolCall",
        (),
        {
            "id": "tool-1",
            "function": type("Function", (), {"name": "invoice_ocr", "arguments": '{"invoice_path":"data/uploads/a.pdf"}'})(),
        },
    )()

    ai_chat = AsyncMock(side_effect=[("先识别发票", [tool_call]), ("识别完成", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)
    monkeypatch.setattr(
        "client.web_server_chat._fetch_available_tools",
        AsyncMock(
            return_value=[
                {
                    "type": "function",
                    "function": {"name": "invoice_ocr", "description": "OCR", "parameters": {"type": "object"}},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._fetch_registered_rpas_from_session",
        AsyncMock(
            return_value=[
                {
                    "id": "invoice_ocr",
                    "description": "OCR tool",
                    "tool_profile": {
                        "solves": ["识别发票内容"],
                        "outputs": ["OCR 文本"],
                    },
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["识别发票"],
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._execute_tool_call",
        AsyncMock(return_value=('{"status":"success","data":{"text":"ok"}}', "success", None)),
    )

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "请帮我识别发票"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert '"type": "runtime_plan"' in joined
    assert '"type": "reusable_task_suggestion"' in joined
    assert "识别发票" in joined


@pytest.mark.asyncio
async def test_chat_stream_blocks_final_answer_when_required_tool_not_called(monkeypatch):
    ai_chat = AsyncMock(
        side_effect=[
            ("这个自定义流程我已经帮你完成。", []),
            ("这个自定义流程我已经帮你完成。", []),
            ("这个自定义流程我已经帮你完成。", []),
        ]
    )
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)
    monkeypatch.setattr(
        "client.web_server_chat._fetch_available_tools",
        AsyncMock(
            return_value=[
                    {
                        "type": "function",
                        "function": {
                        "name": "custom_automation",
                        "description": "Custom tool",
                        "parameters": {"type": "object"},
                        },
                    }
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._fetch_registered_rpas_from_session",
        AsyncMock(
            return_value=[
                {
                    "id": "custom_automation",
                    "description": "Custom tool",
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["自定义流程"],
                    },
                }
            ]
        ),
    )

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "请帮我执行自定义流程"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert "需要先调用这些自动化工具：custom_automation" in joined
    assert "不会直接输出“已完成”的结果" in joined


@pytest.mark.asyncio
async def test_chat_with_mcp_routes_required_tools_in_stages(monkeypatch):
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr(
        "client.web_server_chat._fetch_available_tools",
        AsyncMock(
            return_value=[
                {
                    "type": "function",
                    "function": {"name": "invoice_ocr", "description": "OCR", "parameters": {"type": "object"}},
                },
                {
                    "type": "function",
                    "function": {"name": "send_email", "description": "mail", "parameters": {"type": "object"}},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._fetch_registered_rpas_from_session",
        AsyncMock(
            return_value=[
                {
                    "id": "invoice_ocr",
                    "description": "OCR tool",
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["识别发票"],
                    },
                },
                {
                    "id": "send_email",
                    "description": "mail tool",
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["发送邮件"],
                    },
                },
            ]
        ),
    )
    tool_call_ocr = type(
        "ToolCall",
        (),
        {
            "id": "tool-ocr",
            "function": type("Function", (), {"name": "invoice_ocr", "arguments": '{"invoice_path":"invoice.png"}'})(),
        },
    )()
    tool_call_email = type(
        "ToolCall",
        (),
        {
            "id": "tool-email",
            "function": type(
                "Function",
                (),
                {"name": "send_email", "arguments": '{"to":"demo@example.com","subject":"结果","body":"完成"}'},
            )(),
        },
    )()
    ai_chat = AsyncMock(side_effect=[("先识别发票", [tool_call_ocr]), ("再发送邮件", [tool_call_email]), ("done", [])])
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)
    monkeypatch.setattr(
        "client.web_server_chat._execute_tool_call",
        AsyncMock(
            side_effect=[
                ('{"status":"success","data":{"text":"ocr ok"}}', "success", None),
                ('{"status":"success","data":{"sent":true}}', "success", None),
            ]
        ),
    )

    result = await chat_with_mcp(
        [{"role": "user", "content": "请帮我识别发票并发送邮件到 demo@example.com"}],
        "deepseek",
    )

    assert result["response"] == "done"
    assert ai_chat.await_count == 3

    first_call = ai_chat.await_args_list[0].kwargs
    assert [tool["function"]["name"] for tool in first_call["tools"]] == ["invoice_ocr"]
    assert first_call["tool_choice"] == {"type": "function", "function": {"name": "invoice_ocr"}}

    second_call = ai_chat.await_args_list[1].kwargs
    assert [tool["function"]["name"] for tool in second_call["tools"]] == ["send_email"]
    assert second_call["tool_choice"] == {"type": "function", "function": {"name": "send_email"}}


@pytest.mark.asyncio
async def test_chat_stream_stops_required_chain_after_pending_tool(monkeypatch):
    tool_call = type(
        "ToolCall",
        (),
        {
            "id": "tool-ocr",
            "function": type("Function", (), {"name": "invoice_ocr", "arguments": '{"invoice_path":"a.pdf"}'})(),
        },
    )()

    ai_chat = AsyncMock(side_effect=[("先执行 OCR", [tool_call]), ("后台执行中", [])])
    monkeypatch.setattr("client.web_server_chat.sse_client", lambda *_args, **_kwargs: DummySSEClient())
    monkeypatch.setattr("client.web_server_chat.ClientSession", DummySession)
    monkeypatch.setattr("client.web_server_chat.ai_manager.chat", ai_chat)
    monkeypatch.setattr(
        "client.web_server_chat._fetch_available_tools",
        AsyncMock(
            return_value=[
                {
                    "type": "function",
                    "function": {"name": "invoice_ocr", "description": "OCR", "parameters": {"type": "object"}},
                },
                {
                    "type": "function",
                    "function": {"name": "send_email", "description": "mail", "parameters": {"type": "object"}},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._fetch_registered_rpas_from_session",
        AsyncMock(
            return_value=[
                {
                    "id": "invoice_ocr",
                    "description": "OCR tool",
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["识别发票"],
                    },
                },
                {
                    "id": "send_email",
                    "description": "mail tool",
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["发送邮件"],
                    },
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "client.web_server_chat._execute_tool_call",
        AsyncMock(return_value=('{"status":"pending","task_id":"task-123"}', "pending", "task-123")),
    )

    response = await chat_stream(
        ChatRequest(messages=[{"role": "user", "content": "请帮我识别发票并发送邮件"}], provider="deepseek")
    )

    body = []
    async for chunk in response.body_iterator:
        body.append(chunk)

    joined = "".join(body)
    assert '"task_id": "task-123"' in joined
    assert "后台执行中" in joined

    first_call = ai_chat.await_args_list[0].kwargs
    assert first_call["tool_choice"] == {"type": "function", "function": {"name": "invoice_ocr"}}

    second_call = ai_chat.await_args_list[1].kwargs
    assert second_call["tools"] is None
    assert second_call["tool_choice"] is None


def test_login_sets_secure_cookie_on_https(monkeypatch):
    monkeypatch.setattr(
        "client.web_server.authenticate_user",
        AsyncMock(return_value={"id": "user-1", "username": "demo", "role": "business", "display_name": "演示账号", "assigned_rpa_ids": []}),
    )
    monkeypatch.setattr("client.web_server.create_session", AsyncMock(return_value="token-123"))

    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/login", json={"username": "demo", "password": "strongpass123"})

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
