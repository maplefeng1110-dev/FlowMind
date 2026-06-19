from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, Optional

from fastapi import WebSocket

from .broker import Broker
from .db import create_task, get_task, register_machine, update_machine_status, update_task_result
from .kv import build_kv

logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {"success", "error", "timeout", "failed"}


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
    ):
        self.connections[machine_id] = ws
        await register_machine(machine_id, system_info, rpas, manifests)
        await self.broker.machines.set_location(machine_id, self.instance_id)
        logger.info("[+] Agent online: %s (instance %s)", machine_id, self.instance_id)

    async def disconnect(self, machine_id: str):
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
            await update_task_result(task_id, "error", {"status": "error", "message": "Failed to dispatch task to agent", "error": str(exc)})
            raise RuntimeError(f"Failed to dispatch async task {task_id} to {machine_id}: {exc}") from exc
        if not delivered:
            await update_task_result(task_id, "error", {"status": "error", "message": "Machine offline", "error": f"Machine offline: {machine_id}"})
            raise RuntimeError(f"Machine offline: {machine_id}")
        logger.info("Async task %s dispatched to %s", task_id, machine_id)
        return {"status": "accepted", "task_id": task_id}

    async def resolve(self, task_id: str, result: dict):
        task_status, normalized_result = _normalize_task_result(result)
        self._task_machines.pop(task_id, None)
        await update_task_result(task_id, task_status, normalized_result)
        await self.broker.results.publish(task_id, normalized_result)

    def _fail(self, task_id: str) -> None:
        self.broker.results.discard(task_id)
        self._task_machines.pop(task_id, None)


engine = DispatchEngine()
