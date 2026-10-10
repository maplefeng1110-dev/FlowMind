"""A reconnecting Agent reports the tasks it still has; the Registry settles the rest."""
import aiosqlite
import pytest
import pytest_asyncio

import registry.db as registry_db
from registry.dispatch import DispatchEngine

MANIFESTS = [{"id": "web_query", "params_schema": {"type": "object"}}]


class _Socket:
    async def send_text(self, text):
        pass


@pytest_asyncio.fixture
async def engine(monkeypatch, tmp_path):
    monkeypatch.setattr(registry_db, "DB_PATH", str(tmp_path / "registry.db"))
    await registry_db.init_db()
    return DispatchEngine()


async def _task(task_id, machine_id="m1", status="running", age_seconds=60):
    await registry_db.create_task(task_id, machine_id, "web_query", {}, status=status)
    async with aiosqlite.connect(registry_db.DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET created_at = datetime('now', ?) WHERE id = ?", (f"-{age_seconds} seconds", task_id)
        )
        await db.commit()


async def _status(task_id):
    return (await registry_db.get_task(task_id))["status"]


@pytest.mark.asyncio
async def test_reconnecting_agent_settles_the_tasks_it_no_longer_has(engine):
    await _task("t-kept")
    await _task("t-lost")
    await _task("t-done", status="success")
    await _task("t-other", machine_id="m2")
    await _task("t-fresh", age_seconds=0)  # may still be on its way to the Agent
    await _task("t-waiting", age_seconds=0)
    engine._task_machines["t-waiting"] = "m1"  # sent over the old connection; a sync call waits on it
    waiter = engine.broker.results.expect("t-waiting")

    await engine.register("m1", _Socket(), {}, ["web_query"], MANIFESTS, running_task_ids=["t-kept"])

    assert await _status("t-lost") == "error"
    assert (await registry_db.get_task("t-lost"))["result"]["error"] == "Task lost"
    assert await _status("t-waiting") == "error"
    assert waiter.done() and waiter.result()["error"] == "Task lost"
    assert "t-waiting" not in engine._task_machines
    for unaffected, status in (("t-kept", "running"), ("t-done", "success"), ("t-other", "running"), ("t-fresh", "running")):
        assert await _status(unaffected) == status


@pytest.mark.asyncio
async def test_agents_that_do_not_report_are_left_as_before(engine):
    await _task("t-old")

    await engine.register("m1", _Socket(), {}, ["web_query"], MANIFESTS)

    assert await _status("t-old") == "running"


@pytest.mark.asyncio
async def test_stale_connection_closing_does_not_evict_the_reconnected_agent(engine):
    old, new = _Socket(), _Socket()
    await engine.register("m1", old, {}, ["web_query"], MANIFESTS, running_task_ids=[])
    await _task("t-run")
    engine._task_machines["t-run"] = "m1"
    await engine.register("m1", new, {}, ["web_query"], MANIFESTS, running_task_ids=["t-run"])

    await engine.disconnect("m1", old)  # the old socket's close arrives late

    assert engine.connections["m1"] is new
    assert (await registry_db.get_machine("m1"))["status"] == "online"
    assert await _status("t-run") == "running"
    assert engine._task_machines["t-run"] == "m1"

    await engine.disconnect("m1", new)

    assert "m1" not in engine.connections
    assert await _status("t-run") == "error"
