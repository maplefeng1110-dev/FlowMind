"""A result that finishes while the Agent is reconnecting still reaches the Registry."""
import asyncio
import contextlib
import json
import types

import pytest

import agent.main as agent_main


class _Socket:
    def __init__(self, name, broken=False):
        self.name = name
        self.broken = broken


@pytest.fixture
def agent(monkeypatch):
    state = types.SimpleNamespace(sent=[], registered=[], gates={})

    async def run(rpa_id, params, task_id=None):
        gate = state.gates.get(task_id)
        if gate:
            await gate.wait()
        return {"status": "success", "data": {"task": task_id}}

    async def send(ws, payload, _lock):
        if ws.broken:
            raise ConnectionError(f"{ws.name} is closed")
        if payload.get("type") == "result":
            state.sent.append((ws.name, payload["task_id"]))
        elif payload.get("type") == "register":
            state.registered.append((ws.name, payload.get("running_task_ids")))

    executor = types.SimpleNamespace(manifests={"web_query": {"id": "web_query"}}, plugins={"web_query": object()}, run=run)
    monkeypatch.setattr(agent_main, "executor", executor)
    monkeypatch.setattr(agent_main, "_send_json", send)
    monkeypatch.setattr(agent_main, "_exclusive_locks", {})
    monkeypatch.setattr(agent_main, "_running_tasks", set())
    monkeypatch.setattr(agent_main, "_inflight_task_ids", set(), raising=False)
    monkeypatch.setattr(agent_main, "_link", {"ws": None, "send_lock": None, "up": None}, raising=False)
    monkeypatch.setattr(agent_main, "_build_system_info", lambda: {})
    return state


def _start(task_id):
    return agent_main._start_task({"task_id": task_id, "rpa_id": "web_query", "params": {}})


@pytest.mark.asyncio
async def test_result_finished_while_disconnected_goes_out_on_the_next_connection(agent):
    old = _Socket("old")
    agent_main._attach(old, asyncio.Lock())
    agent.gates["t-1"] = asyncio.Event()
    job = _start("t-1")
    await asyncio.sleep(0)

    agent_main._detach(old)  # the connection drops while the task runs
    agent.gates["t-1"].set()
    await asyncio.sleep(0.05)
    assert agent.sent == [] and not job.done()

    agent_main._attach(_Socket("new"), asyncio.Lock())
    await asyncio.wait_for(job, timeout=1)
    assert agent.sent == [("new", "t-1")]


@pytest.mark.asyncio
async def test_a_failed_send_waits_for_the_reconnect(agent):
    agent_main._attach(_Socket("old", broken=True), asyncio.Lock())
    job = _start("t-2")
    await asyncio.sleep(0.05)
    assert agent.sent == [] and not job.done()

    agent_main._attach(_Socket("new"), asyncio.Lock())
    await asyncio.wait_for(job, timeout=1)
    assert agent.sent == [("new", "t-2")]


@pytest.mark.asyncio
async def test_result_is_dropped_when_no_connection_comes_back_in_time(agent, monkeypatch):
    monkeypatch.setattr(agent_main, "RESULT_DELIVERY_WAIT_SEC", 0.05)

    await asyncio.wait_for(_start("t-3"), timeout=1)

    assert agent.sent == []
    assert agent_main._inflight_task_ids == set()


@pytest.mark.asyncio
async def test_registration_lists_the_tasks_not_yet_reported(agent):
    agent_main._attach(_Socket("live"), asyncio.Lock())
    agent.gates["t-4"] = asyncio.Event()
    job = _start("t-4")
    await asyncio.sleep(0)

    assert agent_main._build_register_payload()["running_task_ids"] == ["t-4"]

    agent.gates["t-4"].set()
    await asyncio.wait_for(job, timeout=1)
    assert agent_main._build_register_payload()["running_task_ids"] == []


class _Connection(_Socket):
    """Delivers its messages, then closes or stays open."""

    def __init__(self, name, messages=(), stay_open=True):
        super().__init__(name)
        self.messages = list(messages)
        self.stay_open = stay_open

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.broken = True
        return False

    def __aiter__(self):
        return self._stream()

    async def _stream(self):
        for message in self.messages:
            yield json.dumps(message)
        if self.stay_open:
            await asyncio.Event().wait()


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_agent_loop_reports_the_task_and_delivers_it_after_reconnecting(agent, monkeypatch):
    agent.gates["t-5"] = asyncio.Event()
    first = _Connection("first", [{"task_id": "t-5", "rpa_id": "web_query", "params": {}}], stay_open=False)
    connections = iter([first, _Connection("second")])
    monkeypatch.setattr(agent_main, "websockets", types.SimpleNamespace(connect=lambda *a, **k: next(connections)))
    runner = asyncio.create_task(agent_main.run_agent())
    try:
        await asyncio.wait_for(_until(lambda: len(agent.registered) == 2), timeout=1)
        assert agent.registered == [("first", []), ("second", ["t-5"])]

        agent.gates["t-5"].set()  # the task finishes after the reconnect
        await asyncio.wait_for(_until(lambda: agent.sent), timeout=1)
        assert agent.sent == [("second", "t-5")]
    finally:
        agent.gates["t-5"].set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        await asyncio.gather(*agent_main._running_tasks, return_exceptions=True)
