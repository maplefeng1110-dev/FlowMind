from unittest.mock import AsyncMock

import pytest

from client import schedule_runtime


@pytest.mark.asyncio
async def test_execute_claimed_schedule_completes_and_revokes_session(monkeypatch):
    calls = []

    async def fake_proxy_json(method, path, json_body=None, **kwargs):
        calls.append({"method": method, "path": path, "json_body": json_body, "kwargs": kwargs})
        if path == "/internal/schedule-sessions":
            return {"session_token": "session-1"}
        if path.endswith("/start"):
            return {"id": "run-1", "status": "running"}
        if path == "/history/conversations":
            return {"status": "success"}
        if path == "/history/messages":
            return {"status": "success"}
        if path.endswith("/complete"):
            return {"id": "run-1", "status": json_body["status"]}
        if path == "/internal/schedule-sessions/revoke":
            return {"status": "success"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr("client.schedule_runtime._proxy_json", fake_proxy_json)
    monkeypatch.setattr(
        "client.schedule_runtime.chat_with_mcp",
        AsyncMock(
            return_value={
                "response": "日报已发送",
                "provider": "deepseek",
                "runtime_plan": {"status": "completed"},
                "tool_trace": [{"name": "send_email", "status": "success"}],
            }
        ),
    )

    await schedule_runtime.execute_claimed_schedule(
        {
            "claim_token": "claim-1",
            "schedule": {
                "id": "schedule-1",
                "title": "财务日报",
                "prompt": "整理日报并发给财务",
                "provider": "deepseek",
                "schedule_type": "daily",
                "owner_user_id": "user-1",
            },
            "run": {
                "id": "run-1",
                "planned_for": "2026-03-30 09:00:00",
            },
        }
    )

    complete_call = next(item for item in calls if item["path"].endswith("/complete"))
    assert complete_call["json_body"]["status"] == "success"
    assert complete_call["json_body"]["conversation_id"]
    assert complete_call["json_body"]["tool_trace"][0]["name"] == "send_email"

    revoke_call = next(item for item in calls if item["path"] == "/internal/schedule-sessions/revoke")
    assert revoke_call["json_body"] == {"session_token": "session-1"}
