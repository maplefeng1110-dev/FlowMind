"""With sync tools the model reads one tool's real result and uses it in the next call."""
import asyncio
import json
import types
from contextlib import asynccontextmanager

from fastapi import HTTPException

import client.web_server_chat as chat
import client.web_server_tooling as tooling

RPAS = [
    {"id": "excel_processor", "execution": "sync", "params_schema": {"type": "object"}},
    {"id": "send_email", "execution": "sync", "params_schema": {"type": "object"}},
]


class _Session:
    def __init__(self, *args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None


def _call(call_id, name, args):
    function = types.SimpleNamespace(name=name, arguments=json.dumps(args))
    return types.SimpleNamespace(id=call_id, function=function)


def _install_fakes(monkeypatch, llm_calls, dispatched):
    @asynccontextmanager
    async def sse(*args, **kwargs):
        yield (None, None)

    async def registered(session, available_only=True):
        return RPAS

    async def tool_list(session):
        return [{"type": "function", "function": {"name": r["id"], "parameters": {}}} for r in RPAS]

    monkeypatch.setattr(chat, "sse_client", sse)
    monkeypatch.setattr(chat, "ClientSession", _Session)
    monkeypatch.setattr(chat, "_fetch_registered_rpas_from_session", registered)
    monkeypatch.setattr(chat, "_fetch_available_tools", tool_list)

    async def fake_llm(provider, messages, system_prompt, tools, tool_choice):
        llm_calls.append([dict(message) for message in messages])
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            return "", [_call("c1", "excel_processor", {"operation": "summary", "file_path": "data/uploads/q3.xlsx"})]
        if len(tool_messages) == 1:
            first = tool_messages[0]["content"]
            if first.startswith("Error"):
                return f"失败：{first}", None
            result = json.loads(first)
            if result.get("status") != "success":
                return "已提交后台任务，完成后再继续", None
            total = result["data"]["total"]
            return "", [_call("c2", "send_email", {"subject": "Q3", "body": f"total={total}"})]
        return "done", None

    monkeypatch.setattr(chat.ai_manager, "chat", fake_llm)

    async def fake_registry(method, path, json_body=None, **kwargs):
        dispatched.append({"path": path, "body": json_body, "timeout": kwargs.get("timeout")})
        if json_body["rpa_id"] == "excel_processor":
            return {"status": "success", "data": {"total": 42}, "task_id": "t-excel"}
        return {"status": "success", "data": {"sent": True}, "task_id": "t-mail"}

    monkeypatch.setattr(tooling, "_proxy_json", fake_registry)


def _chat(text):
    messages = [{"role": "user", "content": text}]
    return asyncio.run(chat._run_chat_loop(messages, "deepseek", None, None, "session", emit_tool_events=False))


def test_model_chains_sync_tools_on_real_results(monkeypatch):
    llm_calls, dispatched = [], []
    _install_fakes(monkeypatch, llm_calls, dispatched)

    reply = _chat("汇总 Q3 表格并把合计发邮件给我")

    assert [d["path"] for d in dispatched] == ["/dispatch/sync", "/dispatch/sync"]
    assert all(d["timeout"] == tooling.SYNC_DISPATCH_TIMEOUT_SECONDS for d in dispatched)
    # The second call was built from the first call's real result, within the same turn.
    assert dispatched[1]["body"]["params"]["body"] == "total=42"
    assert len(llm_calls) == 3
    assert reply["response"] == "done"
    assert [(t["name"], t["status"], t["task_id"]) for t in reply["tool_trace"]] == [
        ("excel_processor", "success", "t-excel"),
        ("send_email", "success", "t-mail"),
    ]


def test_sync_dispatch_failure_reaches_the_model_as_an_error(monkeypatch):
    llm_calls, dispatched = [], []
    _install_fakes(monkeypatch, llm_calls, dispatched)

    async def offline(method, path, json_body=None, **kwargs):
        dispatched.append({"path": path, "body": json_body})
        raise HTTPException(status_code=503, detail="Machine offline: m1")

    monkeypatch.setattr(tooling, "_proxy_json", offline)

    reply = _chat("汇总 Q3 表格")

    assert [(d["path"], d["body"]["rpa_id"]) for d in dispatched] == [("/dispatch/sync", "excel_processor")]
    assert "Machine offline: m1" in reply["response"]
    assert reply["tool_trace"][0]["status"] == "error"


def test_tools_without_sync_declaration_still_run_in_background(monkeypatch):
    llm_calls, dispatched = [], []
    _install_fakes(monkeypatch, llm_calls, dispatched)
    background = [{key: value for key, value in rpa.items() if key != "execution"} for rpa in RPAS]

    async def registered(session, available_only=True):
        return background

    async def accepted(method, path, json_body=None, **kwargs):
        dispatched.append({"path": path, "body": json_body})
        return {"status": "accepted", "task_id": "t-bg"}

    monkeypatch.setattr(chat, "_fetch_registered_rpas_from_session", registered)
    monkeypatch.setattr(tooling, "_proxy_json", accepted)

    reply = _chat("汇总 Q3 表格")

    # Only a pending ticket comes back, so the model cannot build the follow-up call this turn.
    assert [d["path"] for d in dispatched] == ["/dispatch/async"]
    assert reply["tool_trace"][0]["status"] == "pending"
    assert reply["tool_trace"][0]["task_id"] == "t-bg"