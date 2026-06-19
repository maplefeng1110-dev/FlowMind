"""Distributed dispatch broker built on the KeyValuePubSub abstraction.

Lets several Registry instances coordinate while each Agent keeps a single
WebSocket to whichever instance it connected to:

* ``MachineRegistry`` -- shared "which Agent is on which instance" table.
* ``ResultBus``       -- a task result is awaited on the instance that dispatched
  it; the instance that owns the Agent publishes the result back so the waiter's
  future resolves (cross-instance).
* ``TaskRouter``      -- a dispatch targeting an Agent on another instance is
  forwarded over that instance's channel.
* ``DistributedLock`` -- SET NX PX acquire + compare-and-delete release.

With MemoryKV (no Redis) everything runs in-process; dispatch+resolve on the same
instance hit the local fast path (future resolved directly, no publish), so the
single-instance behaviour is unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import suppress
from typing import Awaitable, Callable, Dict, Optional

from .kv import KeyValuePubSub

logger = logging.getLogger(__name__)

RESULT_CHANNEL = "flowmind:results"
TASK_CHANNEL_PREFIX = "flowmind:tasks:"
MACHINE_TABLE = "flowmind:machines"  # machine_id -> instance_id
LOCK_PREFIX = "flowmind:lock:"


class DistributedLock:
    """Mutual exclusion across instances (SET NX PX + safe compare-and-delete)."""

    def __init__(self, kv: KeyValuePubSub):
        self._kv = kv

    async def acquire(self, key: str, ttl_ms: int = 10000) -> Optional[str]:
        token = uuid.uuid4().hex
        if await self._kv.set_nx(LOCK_PREFIX + key, token, ttl_ms):
            return token
        return None

    async def release(self, key: str, token: str) -> bool:
        return await self._kv.delete_if(LOCK_PREFIX + key, token)


class MachineRegistry:
    """Shared table mapping machine_id -> the instance that owns its WebSocket."""

    def __init__(self, kv: KeyValuePubSub):
        self._kv = kv

    async def set_location(self, machine_id: str, instance_id: str) -> None:
        await self._kv.hset(MACHINE_TABLE, machine_id, instance_id)

    async def get_location(self, machine_id: str) -> Optional[str]:
        return await self._kv.hget(MACHINE_TABLE, machine_id)

    async def clear(self, machine_id: str) -> None:
        await self._kv.hdel(MACHINE_TABLE, machine_id)


class ResultBus:
    """Awaits task results locally; bridges cross-instance results via pub/sub."""

    def __init__(self, kv: KeyValuePubSub):
        self._kv = kv
        self._pending: Dict[str, "asyncio.Future"] = {}

    def expect(self, task_id: str) -> "asyncio.Future":
        future = asyncio.get_event_loop().create_future()
        self._pending[task_id] = future
        return future

    def discard(self, task_id: str) -> None:
        self._pending.pop(task_id, None)

    def resolve_local(self, task_id: str, result: dict) -> bool:
        future = self._pending.pop(task_id, None)
        if future is not None and not future.done():
            future.set_result(result)
            return True
        return False

    async def publish(self, task_id: str, result: dict) -> None:
        # Fast path: the waiter is on this instance -> resolve directly, no pub/sub.
        if self.resolve_local(task_id, result):
            return
        await self._kv.publish(RESULT_CHANNEL, json.dumps({"task_id": task_id, "result": result}))


class TaskRouter:
    """Forwards a dispatch to the instance that owns the target Agent."""

    def __init__(self, kv: KeyValuePubSub, instance_id: str):
        self._kv = kv
        self.instance_id = instance_id

    async def forward(self, target_instance: str, payload: dict) -> None:
        await self._kv.publish(TASK_CHANNEL_PREFIX + target_instance, json.dumps(payload))


class Broker:
    def __init__(self, kv: KeyValuePubSub, instance_id: Optional[str] = None):
        self.kv = kv
        self.instance_id = instance_id or uuid.uuid4().hex
        self.lock = DistributedLock(kv)
        self.machines = MachineRegistry(kv)
        self.results = ResultBus(kv)
        self.router = TaskRouter(kv, self.instance_id)
        self._on_remote_task: Optional[Callable[[dict], Awaitable[None]]] = None
        self._consumers: list = []
        self._subs: list = []

    def set_remote_task_handler(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        self._on_remote_task = handler

    async def start(self) -> None:
        self._consumers.append(asyncio.ensure_future(self._consume_results()))
        self._consumers.append(asyncio.ensure_future(self._consume_tasks()))

    async def _consume_results(self) -> None:
        sub = self.kv.subscribe(RESULT_CHANNEL)
        self._subs.append(sub)
        async for raw in sub:
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            self.results.resolve_local(data.get("task_id"), data.get("result"))

    async def _consume_tasks(self) -> None:
        sub = self.kv.subscribe(TASK_CHANNEL_PREFIX + self.instance_id)
        self._subs.append(sub)
        async for raw in sub:
            if self._on_remote_task is None:
                continue
            try:
                payload = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            try:
                await self._on_remote_task(payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("remote task handler error: %s", exc)

    async def stop(self) -> None:
        for sub in self._subs:
            with suppress(Exception):
                await sub.close()
        for task in self._consumers:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._consumers.clear()
        self._subs.clear()
        await self.kv.close()
