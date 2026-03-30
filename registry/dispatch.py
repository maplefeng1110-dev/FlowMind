import asyncio
import json
import logging
import uuid
from typing import Dict, List, Optional

from fastapi import WebSocket

from .db import create_task, get_task, register_machine, update_machine_status, update_task_result

logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {"success", "error", "timeout", "failed"}


def _normalize_task_result(result: dict) -> tuple[str, dict]:
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
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}
        self.pending: Dict[str, asyncio.Future] = {}
        self.task_machines: Dict[str, str] = {}

    async def register(
        self,
        machine_id: str,
        ws: WebSocket,
        system_info: dict,
        rpas: list,
        manifests: Optional[List[dict]] = None,
    ):
        self.connections[machine_id] = ws
        await register_machine(machine_id, system_info, rpas, manifests)
        logger.info("[+] Agent online: %s", machine_id)

    async def disconnect(self, machine_id: str):
        self.connections.pop(machine_id, None)
        await update_machine_status(machine_id, "offline")
        logger.info("[-] Agent offline: %s", machine_id)

        task_ids_to_cancel = []
        for task_id, assigned_machine_id in self.task_machines.items():
            if assigned_machine_id != machine_id:
                continue
            task_info = await get_task(task_id)
            if task_info and task_info.get("status") not in TERMINAL_TASK_STATUSES:
                task_ids_to_cancel.append(task_id)

        for task_id in task_ids_to_cancel:
            self.task_machines.pop(task_id, None)
            await update_task_result(task_id, "error", {"status": "error", "message": "Machine disconnected", "error": "Machine disconnected"})
            future = self.pending.pop(task_id, None)
            if future and not future.done():
                future.set_exception(Exception(f"Machine {machine_id} disconnected during execution"))

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
        ws = self.connections.get(machine_id)
        if not ws:
            raise RuntimeError(f"Machine offline: {machine_id}")

        task_id = str(uuid.uuid4())
        await create_task(
            task_id,
            machine_id,
            rpa_id,
            params,
            status="running",
            conversation_id=conversation_id,
            conversation_title=conversation_title,
            owner_user_id=owner_user_id,
        )
        self.task_machines[task_id] = machine_id

        future = asyncio.get_running_loop().create_future()
        self.pending[task_id] = future

        try:
            await ws.send_text(json.dumps({"task_id": task_id, "rpa_id": rpa_id, "params": params}))
        except Exception as exc:
            self.pending.pop(task_id, None)
            self.task_machines.pop(task_id, None)
            await update_task_result(
                task_id,
                "error",
                {
                    "status": "error",
                    "message": "Failed to dispatch task to agent",
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"Failed to dispatch task {task_id} to {machine_id}: {exc}") from exc
        logger.info("Task %s dispatched to %s", task_id, machine_id)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, dict):
                enriched = dict(result)
                enriched.setdefault("task_id", task_id)
                return enriched
            return {"status": "error", "message": "Invalid task result", "error": "Invalid task result", "task_id": task_id}
        except asyncio.TimeoutError:
            self.pending.pop(task_id, None)
            self.task_machines.pop(task_id, None)
            await update_task_result(
                task_id,
                "timeout",
                {"status": "timeout", "message": "Task timed out", "error": "Task timed out"},
            )
            raise RuntimeError(f"Task timed out: {task_id}")

    async def dispatch_async(
        self,
        machine_id: str,
        rpa_id: str,
        params: dict,
        conversation_id: Optional[str] = None,
        conversation_title: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> dict:
        ws = self.connections.get(machine_id)
        if not ws:
            raise RuntimeError(f"Machine offline: {machine_id}")

        task_id = str(uuid.uuid4())
        await create_task(
            task_id,
            machine_id,
            rpa_id,
            params,
            status="running",
            conversation_id=conversation_id,
            conversation_title=conversation_title,
            owner_user_id=owner_user_id,
        )
        self.task_machines[task_id] = machine_id

        try:
            await ws.send_text(json.dumps({"task_id": task_id, "rpa_id": rpa_id, "params": params}))
        except Exception as exc:
            self.task_machines.pop(task_id, None)
            await update_task_result(
                task_id,
                "error",
                {
                    "status": "error",
                    "message": "Failed to dispatch task to agent",
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"Failed to dispatch async task {task_id} to {machine_id}: {exc}") from exc
        logger.info("Async task %s dispatched to %s", task_id, machine_id)
        return {"status": "accepted", "task_id": task_id}

    async def resolve(self, task_id: str, result: dict):
        task_status, normalized_result = _normalize_task_result(result)
        self.task_machines.pop(task_id, None)
        await update_task_result(task_id, task_status, normalized_result)
        future = self.pending.pop(task_id, None)
        if future and not future.done():
            future.set_result(normalized_result)


engine = DispatchEngine()
