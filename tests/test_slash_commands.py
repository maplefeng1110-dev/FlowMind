"""Slash commands go through the same constraint layer as model-chosen tool calls."""
import asyncio
from contextlib import asynccontextmanager

import pytest

import client.web_server_chat as chat

EXCEL = {
    "id": "excel_processor",
    "params_schema": {"type": "object", "required": ["operation", "file_path"]},
    "tool_profile": {"allowed_path_roots": ["data/uploads"]},
}
PURGE = {
    "id": "purge_records",
    "params_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
    "tool_profile": {"requires_confirmation": True, "confirmation_hint": "需要用户确认后才能清理。"},
}


class _FakeSession:
    def __init__(self, *args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None


@pytest.fixture
def executed(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fake_sse_client(*args, **kwargs):
        yield (None, None)

    async def fake_registered(session, available_only=True):
        return [EXCEL, PURGE]

    async def fake_tools(session):
        return [{"type": "function", "function": {"name": rpa["id"]}} for rpa in (EXCEL, PURGE)]

    async def fake_execute(session, tool_name, tool_args, *args, **kwargs):
        calls.append((tool_name, tool_args))
        return '{"status": "pending"}', "pending", "task-1"

    monkeypatch.setattr(chat, "sse_client", fake_sse_client)
    monkeypatch.setattr(chat, "ClientSession", _FakeSession)
    monkeypatch.setattr(chat, "_fetch_registered_rpas_from_session", fake_registered)
    monkeypatch.setattr(chat, "_fetch_available_tools", fake_tools)
    monkeypatch.setattr(chat, "_execute_tool_call", fake_execute)
    return calls


def _run(text):
    messages = [{"role": "user", "content": text}]
    return asyncio.run(chat._maybe_run_slash_command(messages, "deepseek", None, "session", emit_tool_events=False))


def test_missing_required_param_is_blocked(executed):
    reply = _run("/excel_processor operation=summary")
    assert executed == []
    assert "file_path" in reply["response"]
    assert reply["tool_trace"][0]["status"] == "blocked"


def test_confirmation_required_tool_needs_explicit_confirmation(executed):
    blocked = _run("/purge_records target=all")
    assert executed == []
    assert "需要用户确认" in blocked["response"]

    confirmed = _run("/purge_records target=all 确认")
    assert executed == [("purge_records", {"target": "all"})]
    assert confirmed["tool_trace"][0]["status"] == "pending"


def test_valid_command_is_dispatched(executed):
    reply = _run("/excel_processor operation=summary file_path=data/uploads/report.xlsx")
    assert executed == [("excel_processor", {"operation": "summary", "file_path": "data/uploads/report.xlsx"})]
    assert reply["tool_trace"][0]["task_id"] == "task-1"
