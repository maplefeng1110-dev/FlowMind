"""A long browser flow must not hold up other plugins' tasks on the same Agent."""
import asyncio
import contextlib
import json
import types

import pytest

import agent.main as agent_main

MANIFESTS = {
    "rpa_flow": {"id": "rpa_flow", "capabilities": ["browser", "rpa"]},
    "web_flow": {"id": "web_flow", "capabilities": ["browser"]},
    "excel_processor": {"id": "excel_processor", "capabilities": ["excel"]},
    "send_email": {"id": "send_email"},
}


@pytest.fixture
def agent(monkeypatch):
    state = types.SimpleNamespace(sent=[], active={}, peak={}, gates={})

    async def run(rpa_id, params, task_id=None):
        key = agent_main._exclusive_key(rpa_id)
        state.active[key] = state.active.get(key, 0) + 1
        state.peak[key] = max(state.peak.get(key, 0), state.active[key])
        try:
            if params.get("crash"):
                raise RuntimeError("plugin loader exploded")
            gate = state.gates.get(task_id)
            await (gate.wait() if gate else asyncio.sleep(0.01))
            return {"status": "success", "data": {"task": task_id}}
        finally:
            state.active[key] -= 1

    async def send(_ws, payload, _lock):
        state.sent.append(payload)

    executor = types.SimpleNamespace(manifests=MANIFESTS, plugins={rpa_id: object() for rpa_id in MANIFESTS}, run=run)
    monkeypatch.setattr(agent_main, "executor", executor)
    monkeypatch.setattr(agent_main, "_send_json", send)
    monkeypatch.setattr(agent_main, "_exclusive_locks", {}, raising=False)
    monkeypatch.setattr(agent_main, "_running_tasks", set(), raising=False)
    return state


def _start(task_id, rpa_id, **params):
    return agent_main._start_task(object(), {"task_id": task_id, "rpa_id": rpa_id, "params": params}, asyncio.Lock())


@pytest.mark.asyncio
async def test_quick_tool_finishes_while_a_browser_flow_is_still_running(agent):
    agent.gates["t-flow"] = asyncio.Event()
    flow = _start("t-flow", "rpa_flow")
    excel = _start("t-excel", "excel_processor", operation="summary")

    await asyncio.wait_for(excel, timeout=1)
    assert [p["task_id"] for p in agent.sent] == ["t-excel"]
    assert not flow.done()

    agent.gates["t-flow"].set()
    await asyncio.wait_for(flow, timeout=1)
    assert [p["task_id"] for p in agent.sent] == ["t-excel", "t-flow"]
    assert not agent_main._running_tasks


@pytest.mark.asyncio
async def test_browser_plugins_take_turns_and_a_plugin_never_overlaps_itself(agent):
    jobs = [
        _start("t1", "rpa_flow"),
        _start("t2", "web_flow"),
        _start("t3", "rpa_flow"),
        _start("t4", "send_email"),
        _start("t5", "send_email"),
        _start("t6", "excel_processor"),
    ]
    await asyncio.wait_for(asyncio.gather(*jobs), timeout=2)

    assert agent.peak == {"browser": 1, "send_email": 1, "excel_processor": 1}
    assert sorted(p["task_id"] for p in agent.sent) == ["t1", "t2", "t3", "t4", "t5", "t6"]


@pytest.mark.asyncio
async def test_unexpected_agent_failure_is_reported_as_an_error_result(agent):
    await asyncio.wait_for(_start("t-bad", "excel_processor", crash=True), timeout=1)

    (payload,) = agent.sent
    assert payload["type"] == "result" and payload["task_id"] == "t-bad"
    assert payload["result"]["status"] == "error"
    assert "plugin loader exploded" in payload["result"]["error"]


class _Connection:
    """Delivers the given task messages, then stays connected."""

    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._stream()

    async def _stream(self):
        for message in self.messages:
            yield json.dumps(message)
        await asyncio.Event().wait()


async def _until_results(agent, *task_ids):
    while not set(task_ids) <= {p.get("task_id") for p in agent.sent if p.get("type") == "result"}:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_connection_keeps_taking_tasks_while_a_flow_runs(agent, monkeypatch):
    agent.gates["t-flow"] = asyncio.Event()
    messages = [
        {"task_id": "t-flow", "rpa_id": "rpa_flow", "params": {}},
        {"task_id": "t-excel", "rpa_id": "excel_processor", "params": {}},
    ]
    monkeypatch.setattr(agent_main, "websockets", types.SimpleNamespace(connect=lambda *a, **k: _Connection(messages)))
    runner = asyncio.create_task(agent_main.run_agent())
    try:
        await asyncio.wait_for(_until_results(agent, "t-excel"), timeout=1)
        assert "t-flow" not in {p.get("task_id") for p in agent.sent}
    finally:
        agent.gates["t-flow"].set()
        await asyncio.wait_for(_until_results(agent, "t-flow", "t-excel"), timeout=1)
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
