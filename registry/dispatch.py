from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, Optional

from fastapi import WebSocket

from .broker import Broker
from .db import (
    create_task,
    get_task,
    list_unfinished_task_ids,
    register_machine,
    update_machine_status,
    update_task_result,
)
from .kv import build_kv

logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {"success", "error", "timeout", "failed"}
# A task row this young may still be on its way to the Agent, so a reconnecting Agent
# that does not list it yet has not lost it.
LOST_TASK_GRACE_SEC = 10


def _normalize_task_result(result: dict) -> tuple:
    if not isinstance(result, dict):
        invalid = {"status": "error", "message": "Invalid task result", "error": "Invalid task result"}
        return "error", invalid

    status = result.get("status", "error")
    normalized = dict(result)

    if status == "success":
        return "success", normalized
    if status == "timeout":
        normalized.setdefault("message", "Task timed out")
        normalized.setdefault("error", normalized["message"])
        return "timeout", normalized

    normalized["status"] = "error"
    normalized.setdefault("message", result.get("message") or result.get("msg") or result.get("error") or "Task failed")
    normalized.setdefault("error", result.get("error") or result.get("msg") or normalized["message"])
    return "error", normalized


class DispatchEngine:
    """Routes tasks to Agents over WebSocket, coordinating across Registry instances
    through a broker. WebSocket connections stay local to each instance; the broker
    handles the shared online table, cross-instance task forwarding, and result
    delivery. With MemoryKV (no Redis) it degrades to single-instance behaviour."""

    def __init__(self, broker: Optional[Broker] = None):
        self.connections: Dict[str, WebSocket] = {}
        self._task_machines: Dict[str, str] = {}
        self.broker = broker or Broker(build_kv(os.getenv("REDIS_URL")))
        self.instance_id = self.broker.instance_id

    async def start(self) -> None:
        self.broker.set_remote_task_handler(self._handle_remote_task)
        await self.broker.start()

    async def stop(self) -> None:
        await self.broker.stop()

    # -- registration -------------------------------------------------------
    async def register(
        self,
        machine_id: str,
        ws: WebSocket,
        system_info: dict,
        rpas: list,
        manifests: Optional[list] = None,
        running_task_ids: Optional[list] = None,
    ):
        # Before the new connection can receive anything, settle what the previous one left
        # behind. Agents that predate the report send no list and are left as they were.
        if isinstance(running_task_ids, list):
            try:
                await self._fail_lost_tasks(machine_id, {str(task_id) for task_id in running_task_ids})
            except Exception:
                logger.exception("Could not settle lost tasks for %s", machine_id)
        self.connections[machine_id] = ws
        await register_machine(machine_id, system_info, rpas, manifests)
        await self.broker.machines.set_location(machine_id, self.instance_id)
        logger.info("[+] Agent online: %s (instance %s)", machine_id, self.instance_id)

    async def _fail_lost_tasks(self, machine_id: str, still_running: set) -> None:
        """The Agent reconnected and listed the tasks it still has; the rest of its
        unfinished tasks went down with the old connection or a restart."""

        lost = [task_id for task_id, owner in self._task_machines.items() if owner == machine_id]
        lost += await list_unfinished_task_ids(machine_id, LOST_TASK_GRACE_SEC)
        failure = {
            "status": "error",
            "message": "Task lost: the Agent restarted or lost its connection before reporting a result",
            "error": "Task lost",
        }
        for task_id in dict.fromkeys(lost):
            if task_id in still_running:
                continue
            self._task_machines.pop(task_id, None)
            await update_task_result(task_id, "error", failure)
            await self.broker.results.publish(task_id, failure)
            logger.warning("Task %s on %s was lost; marked as failed", task_id, machine_id)

    async def disconnect(self, machine_id: str, ws: Optional[WebSocket] = None):
        if ws is not None and self.connections.get(machine_id) is not ws:
            # The Agent already reconnected and its new registration settled the tasks
            # of this connection; cleaning up here would evict the live one.
            logger.info("Stale connection of %s closed", machine_id)
            return
        self.connections.pop(machine_id, None)
        await update_machine_status(machine_id, "offline")
        # Only clear the shared location if it still points at this instance.
        if await self.broker.machines.get_location(machine_id) == self.instance_id:
            await self.broker.machines.clear(machine_id)
        logger.info("[-] Agent offline: %s", machine_id)

        task_ids_to_cancel = []
        for task_id, assigned_machine_id in list(self._task_machines.items()):
            if assigned_machine_id != machine_id:
                continue
            task_info = await get_task(task_id)
            if task_info and task_info.get("status") not in TERMINAL_TASK_STATUSES:
                task_ids_to_cancel.append(task_id)

        for task_id in task_ids_to_cancel:
            self._task_machines.pop(task_id, None)
            failure = {"status": "error", "message": "Machine disconnected", "error": "Machine disconnected"}
            await update_task_result(task_id, "error", failure)
            await self.broker.results.publish(task_id, failure)

    # -- delivery -----------------------------------------------------------
    async def _online_elsewhere(self, machine_id: str) -> bool:
        location = await self.broker.machines.get_location(machine_id)
        return bool(location) and location != self.instance_id

    async def _deliver(self, payload: dict) -> bool:
        """Send the task to the Agent: locally if connected here, else forward to the
        instance that owns it. Returns True if delivered (or forwarded)."""
        machine_id = payload["machine_id"]
        ws = self.connections.get(machine_id)
        if ws is not None:
            self._task_machines[payload["task_id"]] = machine_id
            await ws.send_text(
                json.dumps({"task_id": payload["task_id"], "rpa_id": payload["rpa_id"], "params": payload["params"]})
            )
            return True
        if await self._online_elsewhere(machine_id):
            await self.broker.router.forward(await self.broker.machines.get_location(machine_id), payload)
            return True
        return False

    async def _handle_remote_task(self, payload: dict) -> None:
        """A task forwarded from another instance because this instance owns the Agent."""
        machine_id = payload.get("machine_id")
        task_id = payload.get("task_id")
        ws = self.connections.get(machine_id)
        if ws is None:
            offline = {"status": "error", "message": "Machine offline", "error": "Machine offline"}
            await self.broker.results.publish(task_id, offline)
            return
        self._task_machines[task_id] = machine_id
        await ws.send_text(json.dumps({"task_id": task_id, "rpa_id": payload["rpa_id"], "params": payload["params"]}))

    # -- dispatch -----------------------------------------------------------
    async def dispatch(
        self,
        machine_id: str,
        rpa_id: str,
        params: dict,
        timeout: int = 120,
        conversation_id: Optional[str] = None,
        conversation_title: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> dict:
        if machine_id not in self.connections and not await self._online_elsewhere(machine_id):
            raise RuntimeError(f"Machine offline: {machine_id}")

        task_id = str(uuid.uuid4())
        await create_task(
            task_id, machine_id, rpa_id, params, status="running",
            conversation_id=conversation_id, conversation_title=conversation_title, owner_user_id=owner_user_id,
        )
        future = self.broker.results.expect(task_id)
        payload = {"task_id": task_id, "rpa_id": rpa_id, "params": params, "machine_id": machine_id}

        try:
            delivered = await self._deliver(payload)
        except Exception as exc:
            self._fail(task_id)
            await update_task_result(task_id, "error", {"status": "error", "message": "Failed to dispatch task to agent", "error": str(exc)})
            raise RuntimeError(f"Failed to dispatch task {task_id} to {machine_id}: {exc}") from exc
        if not delivered:
            self._fail(task_id)
            await update_task_result(task_id, "error", {"status": "error", "message": "Machine offline", "error": f"Machine offline: {machine_id}"})
            raise RuntimeError(f"Machine offline: {machine_id}")
        logger.info("Task %s dispatched to %s", task_id, machine_id)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._fail(task_id)
            await update_task_result(task_id, "timeout", {"status": "timeout", "message": "Task timed out", "error": "Task timed out"})
            raise RuntimeError(f"Task timed out: {task_id}")

        if isinstance(result, dict):
            enriched = dict(result)
            enriched.setdefault("task_id", task_id)
            return enriched
        return {"status": "error", "message": "Invalid task result", "error": "Invalid task result", "task_id": task_id}

    async def dispatch_async(
        self,
        machine_id: str,
        rpa_id: str,
        params: dict,
        conversation_id: Optional[str] = None,
        conversation_title: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> dict:
        if machine_id not in self.connections and not await self._online_elsewhere(machine_id):
            raise RuntimeError(f"Machine offline: {machine_id}")

        task_id = str(uuid.uuid4())
        await create_task(
            task_id, machine_id, rpa_id, params, status="running",
            conversation_id=conversation_id, conversation_title=conversation_title, owner_user_id=owner_user_id,
        )
        payload = {"task_id": task_id, "rpa_id": rpa_id, "params": params, "machine_id": machine_id}
        try:
            delivered = await self._deliver(payload)
        except Exception as exc:
            self._fail(task_id)
            await update_task_result(task_id, "error", {"status": "error", "message": "Failed to dispatch task to agent", "error": str(exc)})
            raise RuntimeError(f"Failed to dispatch async task {task_id} to {machine_id}: {exc}") from exc
        if not delivered:
            self._fail(task_id)
            await update_task_result(task_id, "error", {"status": "error", "message": "Machine offline", "error": f"Machine offline: {machine_id}"})
            raise RuntimeError(f"Machine offline: {machine_id}")
        logger.info("Async task %s dispatched to %s", task_id, machine_id)
        return {"status": "accepted", "task_id": task_id}

    async def resolve_from_machine(self, machine_id: str, task_id: str, result: dict) -> bool:
        """Accept a result only from the Agent the task was dispatched to.

        In-flight tasks delivered through this instance (including ones forwarded from
        another instance) are checked in memory; late results fall back to the DB record.
        """
        owner = self._task_machines.get(task_id) if task_id else None
        if owner is None and task_id:
            owner = ((await get_task(str(task_id))) or {}).get("machine_id")
        if not owner or owner != machine_id:
            logger.warning("Ignoring result for task %s reported by machine %s", task_id, machine_id)
            return False
        await self.resolve(task_id, result)
        return True

    async def resolve(self, task_id: str, result: dict):
        task_status, normalized_result = _normalize_task_result(result)
        self._task_machines.pop(task_id, None)
        await update_task_result(task_id, task_status, normalized_result)
        await self.broker.results.publish(task_id, normalized_result)

    def _fail(self, task_id: str) -> None:
        self.broker.results.discard(task_id)
        self._task_machines.pop(task_id, None)


engine = DispatchEngine()
