"""Tests for the distributed dispatch broker (registry/broker.py + kv.py).

Everything runs on MemoryKV, with two brokers/engines sharing one MemoryKV to
simulate multiple Registry instances — no real Redis required. DB calls are
monkeypatched to no-ops. Coroutines are driven via asyncio.run() (no pytest-asyncio).
"""
import asyncio
import json
import sys
import time
import types

import pytest

# registry/db.py uses Python 3.9+ syntax (zoneinfo, runtime X | Y unions). On the
# 3.8 sandbox we stub the db module so the dispatch engine imports; the real 3.9+
# runtime uses the actual db, and DB calls are no-ops in these tests anyway.
if "registry.db" not in sys.modules:
    _db_stub = types.ModuleType("registry.db")

    async def _async_none(*args, **kwargs):
        return None

    for _name in ("create_task", "get_task", "register_machine", "update_machine_status", "update_task_result"):
        setattr(_db_stub, _name, _async_none)
    sys.modules["registry.db"] = _db_stub

from registry.broker import Broker, DistributedLock, MachineRegistry  # noqa: E402
from registry.dispatch import DispatchEngine  # noqa: E402
from registry.kv import MemoryKV  # noqa: E402


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _get_task(*args, **kwargs):
        return None

    for name in ("create_task", "update_task_result", "register_machine", "update_machine_status"):
        monkeypatch.setattr("registry.dispatch." + name, _noop)
    monkeypatch.setattr("registry.dispatch.get_task", _get_task)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(data)


async def _wait_until(pred, timeout=3.0):
    start = time.time()
    while time.time() - start < timeout:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


# -- distributed lock -------------------------------------------------------
def test_distributed_lock_is_mutually_exclusive():
    async def scenario():
        lock = DistributedLock(MemoryKV())
        token = await lock.acquire("k", 10000)
        assert token
        assert await lock.acquire("k", 10000) is None  # already held
        assert await lock.release("k", "wrong-token") is False
        assert await lock.release("k", token) is True
        assert await lock.acquire("k", 10000)  # released -> acquirable again

    run_async(scenario())


def test_lock_expires_after_ttl():
    async def scenario():
        kv = MemoryKV()
        lock = DistributedLock(kv)
        assert await lock.acquire("k", 30)  # 30ms ttl
        await asyncio.sleep(0.05)
        assert await lock.acquire("k", 10000)  # expired -> acquirable

    run_async(scenario())


# -- machine registry -------------------------------------------------------
def test_machine_registry_set_get_clear():
    async def scenario():
        registry = MachineRegistry(MemoryKV())
        await registry.set_location("m1", "A")
        assert await registry.get_location("m1") == "A"
        await registry.clear("m1")
        assert await registry.get_location("m1") is None

    run_async(scenario())


# -- result bus -------------------------------------------------------------
def test_result_bus_local_and_cross_instance():
    async def scenario():
        kv = MemoryKV()
        a = Broker(kv, "A")
        b = Broker(kv, "B")
        await a.start()
        await b.start()
        try:
            # local fast path
            fut1 = a.results.expect("t1")
            await a.results.publish("t1", {"status": "success", "n": 1})
            assert (await asyncio.wait_for(fut1, 1))["n"] == 1

            # cross-instance: A waits, B publishes -> bridged over pub/sub
            fut2 = a.results.expect("t2")
            await b.results.publish("t2", {"status": "success", "n": 2})
            assert (await asyncio.wait_for(fut2, 2))["n"] == 2
        finally:
            await a.stop()
            await b.stop()

    run_async(scenario())


# -- single-instance dispatch (degraded / no Redis) -------------------------
def test_single_instance_dispatch_round_trip():
    async def scenario():
        engine = DispatchEngine(Broker(MemoryKV(), "solo"))
        await engine.start()
        try:
            ws = FakeWS()
            await engine.register("m1", ws, {}, ["rpa1"])
            dispatch_task = asyncio.ensure_future(engine.dispatch("m1", "rpa1", {"x": 1}, timeout=5))
            assert await _wait_until(lambda: ws.sent)
            task_id = json.loads(ws.sent[-1])["task_id"]
            await engine.resolve(task_id, {"status": "success", "data": {"k": 1}})
            result = await dispatch_task
            assert result["status"] == "success"
            assert result["data"] == {"k": 1}
            assert result["task_id"] == task_id
        finally:
            await engine.stop()

    run_async(scenario())


def test_dispatch_offline_machine_raises():
    async def scenario():
        engine = DispatchEngine(Broker(MemoryKV(), "solo"))
        await engine.start()
        try:
            with pytest.raises(RuntimeError):
                await engine.dispatch("ghost", "rpa1", {}, timeout=1)
        finally:
            await engine.stop()

    run_async(scenario())


# -- cross-instance dispatch (the multi-Registry path) ----------------------
def test_cross_instance_dispatch_round_trip():
    async def scenario():
        kv = MemoryKV()  # shared "Redis"
        engine_a = DispatchEngine(Broker(kv, "A"))
        engine_b = DispatchEngine(Broker(kv, "B"))
        await engine_a.start()
        await engine_b.start()
        try:
            # Agent m1 is connected to instance B.
            ws_b = FakeWS()
            await engine_b.register("m1", ws_b, {}, ["rpa1"])

            # Dispatch is initiated on instance A (which has no local m1 socket).
            dispatch_task = asyncio.ensure_future(engine_a.dispatch("m1", "rpa1", {"x": 1}, timeout=5))

            # A forwards over the broker -> B delivers to its local Agent socket.
            assert await _wait_until(lambda: ws_b.sent), "task was not forwarded to instance B"
            task_id = json.loads(ws_b.sent[-1])["task_id"]

            # Agent replies on B; result is bridged back to the waiter on A.
            await engine_b.resolve(task_id, {"status": "success", "data": {"ok": 1}})
            result = await dispatch_task
            assert result["status"] == "success"
            assert result["data"] == {"ok": 1}
            assert result["task_id"] == task_id
        finally:
            await engine_a.stop()
            await engine_b.stop()

    run_async(scenario())
