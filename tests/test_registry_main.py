from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from registry.main import (
    AsyncDispatchRequest,
    _mcp_current_user,
    _build_registry_tool_description,
    _disconnect_stale_machines_once,
    app,
    call_tool,
    delete_machine_api,
    delete_task_api,
    dispatch_async_api,
    dispatch_sync_api,
    get_machines,
    get_tasks,
    list_tools,
    transport,
)
from registry.mcp_transport import SessionBindingError
from utils.internal_api import INTERNAL_API_HEADER, USER_SESSION_HEADER


def _build_request(token: str | None = None, session_token: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((INTERNAL_API_HEADER.lower().encode("ascii"), token.encode("utf-8")))
    if session_token is not None:
        headers.append((USER_SESSION_HEADER.lower().encode("ascii"), session_token.encode("utf-8")))
    return Request({"type": "http", "headers": headers})


@contextmanager
def _mcp_user_context(user: dict):
    token = _mcp_current_user.set(user)
    try:
        yield
    finally:
        _mcp_current_user.reset(token)


def test_build_registry_tool_description_includes_trigger_terms_and_must_call_note():
    description = _build_registry_tool_description(
        {
            "id": "invoice_ocr",
            "description": "从发票图片中提取 OCR 文本。",
            "capabilities": ["ocr", "invoice"],
            "enforcement": {
                "must_call_when_matched": True,
                "intent_keywords": ["识别发票", "发票OCR"],
            },
        }
    )

    assert "从发票图片中提取 OCR 文本。" in description
    assert "触发场景：识别发票、发票OCR" in description
    assert "必须先调用此工具" in description


def test_ws_endpoint_rejects_agent_register_without_valid_token(monkeypatch):
    register_mock = AsyncMock()
    disconnect_mock = AsyncMock()
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", "secret-token")
    monkeypatch.setattr("registry.main.engine.register", register_mock)
    monkeypatch.setattr("registry.main.engine.disconnect", disconnect_mock)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {
                    "type": "register",
                    "machine_id": "machine-a",
                    "system_info": {},
                    "rpas": ["invoice_ocr"],
                    "manifests": [],
                }
            )
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

    register_mock.assert_not_awaited()
    disconnect_mock.assert_not_awaited()


def test_ws_endpoint_accepts_agent_register_with_valid_token(monkeypatch):
    register_mock = AsyncMock()
    monkeypatch.setenv("FLOWMIND_AGENT_WS_TOKEN", "secret-token")
    monkeypatch.setattr("registry.main.engine.register", register_mock)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {
                    "type": "register",
                    "machine_id": "machine-a",
                    "system_info": {"os": "macOS"},
                    "rpas": ["invoice_ocr"],
                    "manifests": [{"id": "invoice_ocr", "params_schema": {"type": "object"}}],
                    "auth_token": "secret-token",
                }
            )

    register_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tools_uses_augmented_registry_descriptions(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_available_rpas",
        AsyncMock(
            return_value=[
                {
                    "id": "web_query",
                    "description": "真实网页查询工具。",
                    "params_schema": {"type": "object", "properties": {}},
                    "capabilities": ["web", "search"],
                    "enforcement": {
                        "must_call_when_matched": True,
                        "intent_keywords": ["联网查询", "网页搜索"],
                    },
                }
            ]
        ),
    )

    with _mcp_user_context({"id": "admin-1", "role": "admin", "assigned_rpa_ids": []}):
        tools = await list_tools()
    tool_by_name = {tool.name: tool for tool in tools}

    assert "list_rpas" in tool_by_name
    assert "dispatch_rpa" in tool_by_name
    assert "web_query" in tool_by_name
    assert "联网查询、网页搜索" in tool_by_name["web_query"].description
    assert "必须先调用此工具" in tool_by_name["web_query"].description


@pytest.mark.asyncio
async def test_call_tool_returns_full_success_payload(monkeypatch):
    monkeypatch.setattr("registry.main.get_rpa", AsyncMock(return_value={"id": "send_email"}))
    monkeypatch.setattr(
        "registry.main.get_available_rpa",
        AsyncMock(
            return_value={
                "id": "send_email",
                "params_schema": {"type": "object", "properties": {}},
            }
        ),
    )
    perform_dispatch = AsyncMock(return_value={"status": "success", "data": {"sent": True}, "message": "ok"})
    monkeypatch.setattr(
        "registry.main._perform_dispatch",
        perform_dispatch,
    )

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["send_email"]}):
        result = await call_tool("send_email", {"to": "demo@example.com"})

    assert '"status": "success"' in result[0].text
    assert '"sent": true' in result[0].text
    perform_dispatch.assert_awaited_once_with(
        "send_email",
        {"to": "demo@example.com"},
        owner_user_id="user-1",
    )


@pytest.mark.asyncio
async def test_call_tool_marks_offline_tool_as_unavailable(monkeypatch):
    monkeypatch.setattr("registry.main.get_rpa", AsyncMock(return_value={"id": "invoice_ocr"}))
    monkeypatch.setattr("registry.main.get_available_rpa", AsyncMock(return_value=None))

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}):
        with pytest.raises(Exception, match="Tool currently unavailable"):
            await call_tool("invoice_ocr", {})


@pytest.mark.asyncio
async def test_list_tools_filters_dynamic_tools_for_business_user(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_available_rpas",
        AsyncMock(
            return_value=[
                {
                    "id": "web_query",
                    "description": "真实网页查询工具。",
                    "params_schema": {"type": "object", "properties": {}},
                },
                {
                    "id": "invoice_ocr",
                    "description": "发票识别工具。",
                    "params_schema": {"type": "object", "properties": {}},
                },
            ]
        ),
    )

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["web_query"]}):
        tools = await list_tools()

    tool_names = {tool.name for tool in tools}
    assert "list_rpas" in tool_names
    assert "dispatch_rpa" not in tool_names
    assert "web_query" in tool_names
    assert "invoice_ocr" not in tool_names


@pytest.mark.asyncio
async def test_call_tool_list_rpas_filters_rpas_for_current_user(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_all_rpas",
        AsyncMock(
            return_value=[
                {"id": "web_query", "tags": ["search"]},
                {"id": "invoice_ocr", "tags": ["ocr"]},
            ]
        ),
    )

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}):
        result = await call_tool("list_rpas", {"available_only": False})

    assert result[0].text == '[{"id": "invoice_ocr", "tags": ["ocr"]}]'


@pytest.mark.asyncio
async def test_call_tool_rejects_unauthorized_rpa_for_business_user(monkeypatch):
    monkeypatch.setattr("registry.main.get_rpa", AsyncMock(return_value={"id": "send_email"}))

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}):
        with pytest.raises(Exception, match="Tool access denied: send_email"):
            await call_tool("send_email", {"to": "demo@example.com"})


@pytest.mark.asyncio
async def test_call_tool_dispatch_rpa_requires_admin_and_uses_current_owner(monkeypatch):
    perform_dispatch = AsyncMock(return_value={"status": "success", "task_id": "task-1"})
    monkeypatch.setattr("registry.main._perform_dispatch", perform_dispatch)

    with _mcp_user_context({"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}):
        with pytest.raises(Exception, match="admin role required"):
            await call_tool("dispatch_rpa", {"rpa_id": "invoice_ocr", "params": {"invoice_path": "a.pdf"}})

    with _mcp_user_context({"id": "admin-1", "role": "admin", "assigned_rpa_ids": []}):
        result = await call_tool("dispatch_rpa", {"rpa_id": "invoice_ocr", "params": {"invoice_path": "a.pdf"}})

    assert '"task_id": "task-1"' in result[0].text
    perform_dispatch.assert_awaited_once_with(
        "invoice_ocr",
        {"invoice_path": "a.pdf"},
        None,
        owner_user_id="admin-1",
    )


def test_transport_validate_session_binding_rejects_mismatch(monkeypatch):
    session_id_hex = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(transport, "_session_tokens", {session_id_hex: "token-a"})

    with pytest.raises(SessionBindingError) as exc_info:
        transport.validate_session_binding(session_id_hex, "token-b")

    assert exc_info.value.status_code == 401


def test_transport_validate_session_binding_accepts_bound_session(monkeypatch):
    session_id_hex = "fedcba9876543210fedcba9876543210"
    monkeypatch.setattr(transport, "_session_tokens", {session_id_hex: "token-a"})

    session_id = transport.validate_session_binding(session_id_hex, "token-a")

    assert session_id.hex == session_id_hex


@pytest.mark.asyncio
async def test_disconnect_stale_machines_once_disconnects_all_stale_agents(monkeypatch):
    monkeypatch.setattr(
        "registry.main.get_stale_online_machine_ids",
        AsyncMock(return_value=["machine-a", "machine-b"]),
    )
    disconnect = AsyncMock()
    monkeypatch.setattr("registry.main.engine.disconnect", disconnect)

    await _disconnect_stale_machines_once()

    assert disconnect.await_count == 2
    disconnect.assert_any_await("machine-a")
    disconnect.assert_any_await("machine-b")


@pytest.mark.asyncio
async def test_registry_tasks_require_session_user_and_scope_by_user(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    list_tasks_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("registry.main.list_tasks", list_tasks_mock)
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(side_effect=lambda token: {"id": "user-1", "role": "business"} if token == "session-1" else None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_tasks(request=_build_request())

    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await get_tasks(request=_build_request("secret"))

    assert exc_info.value.status_code == 401

    result = await get_tasks(request=_build_request("secret", "session-1"))
    assert result == []
    list_tasks_mock.assert_awaited_once_with(limit=50, owner_user_id="user-1", include_all=False)


@pytest.mark.asyncio
async def test_registry_dispatch_async_uses_session_user_as_owner(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    dispatch_async_mock = AsyncMock(return_value={"status": "accepted", "task_id": "task-1"})
    monkeypatch.setattr(
        "registry.main._perform_dispatch_async",
        dispatch_async_mock,
    )
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(
            side_effect=lambda token: (
                {"id": "user-9", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}
                if token == "session-1"
                else None
            )
        ),
    )

    data = AsyncDispatchRequest(rpa_id="invoice_ocr", params={"invoice_path": "a.pdf"}, owner_user_id="forged-user")

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_async_api(data, request=_build_request())

    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_async_api(data, request=_build_request("secret"))

    assert exc_info.value.status_code == 401

    result = await dispatch_async_api(data, request=_build_request("secret", "session-1"))
    assert result["task_id"] == "task-1"
    dispatch_async_mock.assert_awaited_once_with(
        "invoice_ocr",
        {"invoice_path": "a.pdf"},
        None,
        None,
        None,
        "user-9",
    )


@pytest.mark.asyncio
async def test_registry_dispatch_async_rejects_unauthorized_rpa(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    dispatch_async_mock = AsyncMock(return_value={"status": "accepted", "task_id": "task-1"})
    monkeypatch.setattr("registry.main._perform_dispatch_async", dispatch_async_mock)
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(
            return_value={
                "id": "user-9",
                "role": "business",
                "assigned_rpa_ids": ["invoice_ocr"],
            }
        ),
    )

    data = AsyncDispatchRequest(rpa_id="send_email", params={"to": "demo@example.com"})

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_async_api(data, request=_build_request("secret", "session-1"))

    assert exc_info.value.status_code == 403
    dispatch_async_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_dispatch_sync_raises_http_error_for_failed_dispatch(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(
            return_value={
                "id": "user-9",
                "role": "business",
                "assigned_rpa_ids": ["invoice_ocr"],
            }
        ),
    )
    monkeypatch.setattr(
        "registry.main._perform_dispatch",
        AsyncMock(return_value={"status": "error", "message": "No available machines for RPA invoice_ocr"}),
    )

    data = AsyncDispatchRequest(rpa_id="invoice_ocr", params={"invoice_path": "a.pdf"})

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_sync_api(data, request=_build_request("secret", "session-1"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No available machines for RPA invoice_ocr"


@pytest.mark.asyncio
async def test_registry_machines_filters_for_business_session(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(
            side_effect=lambda token: (
                {"id": "admin-1", "role": "admin"} if token == "admin-session"
                else {"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]} if token == "biz-session"
                else None
            )
        ),
    )
    monkeypatch.setattr(
        "registry.main.list_machines",
        AsyncMock(
            return_value=[
                {
                    "id": "machine-a",
                    "status": "online",
                    "last_heartbeat": "2026-03-30 18:00:00",
                    "rpas": ["invoice_ocr", "send_email"],
                    "running_task_count": 1,
                    "system_info": {"agent_admin": {"api_url": "https://agent-a.example.com"}},
                },
                {
                    "id": "machine-b",
                    "status": "online",
                    "last_heartbeat": "2026-03-30 18:01:00",
                    "rpas": ["web_query"],
                    "running_task_count": 0,
                    "system_info": {"agent_admin": {"api_url": "https://agent-b.example.com"}},
                },
                {
                    "id": "machine-c",
                    "status": "offline",
                    "last_heartbeat": "2026-03-30 17:59:00",
                    "rpas": ["invoice_ocr"],
                    "running_task_count": 0,
                    "system_info": {"agent_admin": {"api_url": "https://agent-c.example.com"}},
                },
            ]
        ),
    )

    business_result = await get_machines(request=_build_request("secret", "biz-session"))
    assert business_result == [
        {
            "id": "machine-a",
            "status": "online",
            "last_heartbeat": "2026-03-30 18:00:00",
            "rpas": ["invoice_ocr"],
            "running_task_count": 1,
        }
    ]

    admin_result = await get_machines(request=_build_request("secret", "admin-session"))
    assert len(admin_result) == 3
    assert admin_result[0]["system_info"]["agent_admin"]["api_url"] == "https://agent-a.example.com"


@pytest.mark.asyncio
async def test_registry_delete_task_requires_terminal_status(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(side_effect=lambda token: {"id": "user-1", "role": "business"} if token == "session-1" else None),
    )
    monkeypatch.setattr(
        "registry.main.get_task",
        AsyncMock(
            side_effect=[
                {"id": "task-running", "status": "running", "owner_user_id": "user-1"},
                {"id": "task-success", "status": "success", "owner_user_id": "user-1"},
            ]
        ),
    )
    delete_task_record_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("registry.main.delete_task_record", delete_task_record_mock)

    with pytest.raises(HTTPException) as exc_info:
        await delete_task_api("task-running", request=_build_request("secret", "session-1"))

    assert exc_info.value.status_code == 409

    result = await delete_task_api("task-success", request=_build_request("secret", "session-1"))
    assert result == {"status": "success", "deleted_task_id": "task-success"}
    delete_task_record_mock.assert_awaited_once_with("task-success", owner_user_id="user-1", include_all=False)


@pytest.mark.asyncio
async def test_registry_delete_machine_requires_admin_and_offline_status(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(
            side_effect=lambda token: (
                {"id": "admin-1", "role": "admin"} if token == "admin-session"
                else {"id": "user-1", "role": "business"} if token == "biz-session"
                else None
            )
        ),
    )
    get_machine_mock = AsyncMock(
        side_effect=[
            {"id": "machine-online", "status": "online", "running_task_count": 0},
            {"id": "machine-offline", "status": "offline", "running_task_count": 0},
        ]
    )
    monkeypatch.setattr("registry.main.get_machine", get_machine_mock)
    delete_machine_record_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("registry.main.delete_machine_record", delete_machine_record_mock)

    with pytest.raises(HTTPException) as exc_info:
        await delete_machine_api("machine-online", request=_build_request("secret", "biz-session"))

    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await delete_machine_api("machine-online", request=_build_request("secret", "admin-session"))

    assert exc_info.value.status_code == 409

    result = await delete_machine_api("machine-offline", request=_build_request("secret", "admin-session"))
    assert result == {"status": "success", "deleted_machine_id": "machine-offline"}
    delete_machine_record_mock.assert_awaited_once_with("machine-offline")


def test_registry_create_schedule_uses_common_task_defaults(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    monkeypatch.setattr(
        "registry.main.get_user_by_session_token",
        AsyncMock(return_value={"id": "user-1", "role": "business", "assigned_rpa_ids": ["invoice_ocr"]}),
    )
    monkeypatch.setattr(
        "registry.main.get_common_task",
        AsyncMock(
            return_value={
                "id": "common-1",
                "title": "发票汇总",
                "prompt": "把这些发票识别后汇总到 Excel，再发邮件给财务。",
                "summary": "OCR + Excel + Mail",
            }
        ),
    )
    create_task_schedule_mock = AsyncMock(
        return_value={
            "id": "schedule-1",
            "title": "发票汇总",
            "prompt": "把这些发票识别后汇总到 Excel，再发邮件给财务。",
            "summary": "OCR + Excel + Mail",
            "schedule_type": "daily",
            "schedule_config": {"time": "09:00", "timezone": "Asia/Shanghai"},
            "timezone": "Asia/Shanghai",
            "is_active": True,
            "owner_user_id": "user-1",
        }
    )
    monkeypatch.setattr("registry.main.create_task_schedule", create_task_schedule_mock)

    with TestClient(app) as client:
        response = client.post(
            "/schedules",
            headers={
                INTERNAL_API_HEADER: "secret",
                USER_SESSION_HEADER: "session-1",
            },
            json={
                "common_task_id": "common-1",
                "schedule_type": "daily",
                "schedule_config": {"time": "09:00"},
                "timezone": "Asia/Shanghai",
                "is_active": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["id"] == "schedule-1"
    create_task_schedule_mock.assert_awaited_once()
    kwargs = create_task_schedule_mock.await_args.kwargs
    assert kwargs["title"] == "发票汇总"
    assert kwargs["prompt"].startswith("把这些发票识别后汇总到 Excel")
    assert kwargs["owner_user_id"] == "user-1"


def test_registry_internal_schedule_session_uses_internal_token(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    create_session_mock = AsyncMock(return_value="schedule-session-token")
    monkeypatch.setattr("registry.main.create_internal_session_for_user", create_session_mock)

    with TestClient(app) as client:
        response = client.post(
            "/internal/schedule-sessions",
            headers={INTERNAL_API_HEADER: "secret"},
            json={"user_id": "user-1", "ttl_seconds": 1800},
        )

    assert response.status_code == 200
    assert response.json()["session_token"] == "schedule-session-token"
    create_session_mock.assert_awaited_once_with("user-1", ttl_seconds=1800)


def test_registry_claim_due_schedules_requires_internal_token(monkeypatch):
    monkeypatch.setattr("registry.main.has_internal_api_token", lambda: True)
    monkeypatch.setattr("registry.main.validate_internal_api_token", lambda token: token == "secret")
    claim_mock = AsyncMock(
        return_value=[
            {
                "claim_token": "claim-1",
                "schedule": {"id": "schedule-1"},
                "run": {"id": "run-1", "status": "queued"},
            }
        ]
    )
    monkeypatch.setattr("registry.main.claim_due_task_schedules", claim_mock)

    with TestClient(app) as client:
        response = client.post(
            "/internal/schedules/claim-due",
            headers={INTERNAL_API_HEADER: "secret"},
            json={"limit": 2},
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["run"]["id"] == "run-1"
    claim_mock.assert_awaited_once_with(limit=2)
