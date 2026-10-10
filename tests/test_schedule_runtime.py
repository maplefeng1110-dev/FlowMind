"""A scheduled run only counts as a success once its background RPA tasks succeed."""
import asyncio
from unittest.mock import AsyncMock

import pytest

import client.schedule_runtime as schedule_runtime
from client.orchestration_runtime import build_initial_runtime_plan, register_tool_result, register_tool_start


def _chat_result(task_id="t-1"):
    rpa = {"id": "web_query", "description": "网页查询", "params_schema": {"type": "object"}}
    plan = build_initial_runtime_plan([{"role": "user", "content": "查询网页"}], [rpa])
    plan = register_tool_start(plan, "web_query", {"query": "x"}, rpa)
    plan = register_tool_result(plan, "web_query", "pending", '{"status": "pending"}', task_id)
    return {
        "response": "已提交后台任务。",
        "provider": "deepseek",
        "runtime_plan": plan,
        "tool_trace": [{"id": "c1", "name": "web_query", "status": "pending", "task_id": task_id}],
    }


def _fake_registry(monkeypatch, statuses):
    calls = []

    async def fake_proxy_json(method, path, json_body=None, *, params=None, session_token=None):
        calls.append(path)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        return {"id": path.rsplit("/", 1)[-1], "status": status, "result": {"status": status, "data": {"n": 1}}}

    monkeypatch.setattr(schedule_runtime, "_proxy_json", fake_proxy_json)
    return calls


def _await(result, **kwargs):
    return asyncio.run(schedule_runtime._await_background_tasks(result, "session", poll_seconds=0, **kwargs))


def test_pending_background_task_is_not_a_success():
    assert schedule_runtime._classify_schedule_result(_chat_result())["status"] == "needs_attention"


def test_run_waits_for_task_and_reports_success(monkeypatch):
    calls = _fake_registry(monkeypatch, ["running", "success"])
    result = _await(_chat_result(), timeout_seconds=5)
    assert calls == ["/task/t-1", "/task/t-1"]
    assert result["tool_trace"][0]["status"] == "success"
    assert result["runtime_plan"]["status"] == "completed"
    assert schedule_runtime._classify_schedule_result(result)["status"] == "success"


def test_failed_task_needs_attention(monkeypatch):
    _fake_registry(monkeypatch, ["error"])
    result = _await(_chat_result(), timeout_seconds=5)
    assert result["tool_trace"][0]["status"] == "error"
    assert schedule_runtime._classify_schedule_result(result)["status"] == "needs_attention"


def test_task_still_running_after_wait_needs_attention(monkeypatch):
    _fake_registry(monkeypatch, ["running"])
    result = _await(_chat_result(), timeout_seconds=0)
    assert result["tool_trace"][0]["status"] == "pending"
    assert schedule_runtime._classify_schedule_result(result)["status"] == "needs_attention"


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
