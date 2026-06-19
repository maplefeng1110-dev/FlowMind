import asyncio
import contextvars
import json
import os
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Any, Dict, List, Optional

import mcp.types as types
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from mcp.server import Server
from pydantic import BaseModel
from starlette.responses import Response

from registry.db import (
    claim_due_task_schedules,
    complete_task_schedule_run,
    create_common_task,
    create_internal_session_for_user,
    create_task_schedule,
    delete_machine_record,
    delete_common_task,
    delete_session,
    delete_task_schedule,
    delete_task_record,
    delete_conversation,
    get_all_rpas,
    get_available_rpa,
    get_available_rpas,
    get_common_task,
    get_conversation_messages,
    get_conversations,
    get_machine,
    get_online_machines_for_rpa,
    get_rpa,
    get_task_schedule,
    get_stale_online_machine_ids,
    get_task,
    get_user_by_session_token,
    list_common_tasks,
    list_machines,
    list_task_schedule_runs,
    list_task_schedules,
    list_tasks,
    mark_task_schedule_run_started,
    set_task_schedule_active,
    touch_common_task,
    init_db,
    save_conversation,
    save_message,
    update_task_schedule,
    update_machine_status,
)
from utils.internal_api import INTERNAL_API_HEADER, USER_SESSION_HEADER, has_internal_api_token, validate_internal_api_token
from utils.logger import setup_logger

from .dispatch import TERMINAL_TASK_STATUSES, engine
from .mcp_transport import SessionBoundSseTransport

logger = setup_logger("Registry", "registry.log")

app = FastAPI(title="RPA Registry")

# AI routing gateway exposed to Robot Workers (visual locate / structured extract).
# Additive and self-contained: a failure here must never block registry startup.
try:
    from ai.gateway_api import router as ai_gateway_router

    app.include_router(ai_gateway_router)
except Exception as exc:  # noqa: BLE001
    logger.warning("AI gateway not mounted: %s", exc)

mcp_server = Server("rpa-registry")
HEARTBEAT_TIMEOUT_SEC = max(5, int(os.getenv("AGENT_HEARTBEAT_TIMEOUT_SEC", "45")))
HEARTBEAT_SWEEP_INTERVAL_SEC = max(2, int(os.getenv("AGENT_HEARTBEAT_SWEEP_INTERVAL_SEC", "15")))
_mcp_current_user: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "flowmind_mcp_current_user",
    default=None,
)


def _require_internal_request(request: Request) -> None:
    if not has_internal_api_token():
        raise HTTPException(status_code=500, detail="Internal API token is not configured")
    provided_token = request.headers.get(INTERNAL_API_HEADER)
    if not validate_internal_api_token(provided_token):
        raise HTTPException(status_code=401, detail="Unauthorized internal API request")


def _get_agent_ws_token() -> str:
    return str(os.getenv("FLOWMIND_AGENT_WS_TOKEN", "") or "").strip()


def _is_valid_agent_ws_token(provided_token: Any) -> bool:
    expected_token = _get_agent_ws_token()
    if not expected_token:
        return False
    normalized = str(provided_token or "").strip()
    return bool(normalized) and secrets.compare_digest(normalized, expected_token)


def _build_owner_scope(current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not current_user:
        return {"owner_user_id": None, "include_all": False}
    return {
        "owner_user_id": current_user["id"],
        "include_all": str(current_user.get("role")) == "admin",
    }


async def _resolve_request_user(
    request: Request,
    *,
    require_user: bool = False,
    require_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    _require_internal_request(request)
    session_token = request.headers.get(USER_SESSION_HEADER)
    current_user = await get_user_by_session_token(session_token) if session_token else None

    if require_user and not current_user:
        raise HTTPException(status_code=401, detail="Missing or invalid user session")
    if require_admin:
        if not current_user:
            raise HTTPException(status_code=401, detail="Missing or invalid user session")
        if str(current_user.get("role")) != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _is_admin_user(current_user: Optional[Dict[str, Any]]) -> bool:
    return str((current_user or {}).get("role")) == "admin"


def _get_assigned_rpa_ids(current_user: Optional[Dict[str, Any]]) -> Optional[set[str]]:
    if not current_user:
        return set()
    if _is_admin_user(current_user):
        return None
    return {
        str(rpa_id)
        for rpa_id in current_user.get("assigned_rpa_ids", [])
        if str(rpa_id or "").strip()
    }


def _filter_rpas_for_current_user(
    rpas: List[Dict[str, Any]],
    current_user: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    valid_rpas = [rpa for rpa in (rpas or []) if isinstance(rpa, dict) and rpa.get("id")]
    allowed_rpa_ids = _get_assigned_rpa_ids(current_user)
    if allowed_rpa_ids is None:
        return valid_rpas
    return [rpa for rpa in valid_rpas if rpa["id"] in allowed_rpa_ids]


def _filter_machines_for_current_user(
    machines: List[Dict[str, Any]],
    current_user: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    valid_machines = [machine for machine in (machines or []) if isinstance(machine, dict) and machine.get("id")]
    allowed_rpa_ids = _get_assigned_rpa_ids(current_user)
    if allowed_rpa_ids is None:
        return valid_machines
    if not allowed_rpa_ids:
        return []

    visible_machines: List[Dict[str, Any]] = []
    for machine in valid_machines:
        if str(machine.get("status") or "").lower() != "online":
            continue
        supported_rpas = [
            str(rpa_id)
            for rpa_id in machine.get("rpas", [])
            if str(rpa_id or "").strip()
        ]
        visible_rpas = [rpa_id for rpa_id in supported_rpas if rpa_id in allowed_rpa_ids]
        if not visible_rpas:
            continue
        visible_machines.append(
            {
                "id": machine["id"],
                "status": machine.get("status", "online"),
                "last_heartbeat": machine.get("last_heartbeat"),
                "running_task_count": int(machine.get("running_task_count") or 0),
                "rpas": visible_rpas,
            }
        )
    return visible_machines


def _require_mcp_current_user() -> Dict[str, Any]:
    current_user = _mcp_current_user.get()
    if not current_user:
        raise Exception("Missing MCP user context")
    return current_user


def _require_rpa_access(current_user: Dict[str, Any], rpa_id: str) -> None:
    allowed_rpa_ids = _get_assigned_rpa_ids(current_user)
    if allowed_rpa_ids is None:
        return
    if str(rpa_id or "") not in allowed_rpa_ids:
        raise Exception(f"Tool access denied: {rpa_id}")


def _require_http_rpa_access(current_user: Dict[str, Any], rpa_id: str) -> None:
    allowed_rpa_ids = _get_assigned_rpa_ids(current_user)
    if allowed_rpa_ids is None:
        return
    if str(rpa_id or "") not in allowed_rpa_ids:
        raise HTTPException(status_code=403, detail=f"RPA access denied: {rpa_id}")


def _require_admin_mcp_user(current_user: Dict[str, Any]) -> None:
    if not _is_admin_user(current_user):
        raise Exception("Tool access denied: admin role required")


def _require_session_token(request: Request) -> str:
    session_token = str(request.headers.get(USER_SESSION_HEADER) or "").strip()
    if not session_token:
        raise HTTPException(status_code=401, detail="Missing or invalid user session")
    return session_token


@asynccontextmanager
async def _bind_mcp_request_context(request: Request, current_user: Dict[str, Any], session_token: str):
    async with transport.connect_sse(request.scope, request.receive, request._send, session_token=session_token) as streams:
        token = _mcp_current_user.set(current_user)
        try:
            yield streams
        finally:
            _mcp_current_user.reset(token)


def _build_registry_tool_description(rpa: Dict[str, Any]) -> str:
    base_description = str(rpa.get("description") or "").strip()
    enforcement = rpa.get("enforcement") if isinstance(rpa.get("enforcement"), dict) else {}
    trigger_terms = enforcement.get("intent_keywords")
    if not isinstance(trigger_terms, list) or not trigger_terms:
        trigger_terms = rpa.get("capabilities") if isinstance(rpa.get("capabilities"), list) else []

    description_parts: List[str] = [base_description] if base_description else []
    if trigger_terms:
        joined_terms = "、".join(str(item) for item in trigger_terms[:8] if item)
        if joined_terms:
            description_parts.append(f"触发场景：{joined_terms}")
    if enforcement.get("must_call_when_matched"):
        description_parts.append("重要：命中上述场景时，必须先调用此工具，不能直接声称任务已完成。")
    return "\n".join(part for part in description_parts if part)


def _build_dispatch_error(message: str) -> Dict[str, str]:
    return {
        "status": "error",
        "message": message,
        "error": message,
    }


async def _validate_dispatch_machine(rpa_id: str, machine_id: str | None) -> Optional[Dict[str, str]]:
    normalized_machine_id = str(machine_id or "").strip()
    if not normalized_machine_id:
        return None

    machine = await get_machine(normalized_machine_id)
    if not machine:
        return _build_dispatch_error(f"Machine not found: {normalized_machine_id}")

    if str(machine.get("status") or "").lower() != "online":
        return _build_dispatch_error(f"Machine offline: {normalized_machine_id}")

    machine_rpas = machine.get("rpas")
    if not isinstance(machine_rpas, list):
        machine_rpas = []
    supported_rpa_ids = {str(item) for item in machine_rpas if str(item or "").strip()}
    if str(rpa_id or "") not in supported_rpa_ids:
        return _build_dispatch_error(f"Machine {normalized_machine_id} does not support RPA {rpa_id}")

    return None


def _raise_http_for_dispatch_result(result: Dict[str, Any], expected_status: str) -> None:
    if result.get("status") == expected_status:
        return
    raise HTTPException(status_code=400, detail=result.get("error") or result.get("message") or "Dispatch failed")


@mcp_server.list_tools()
async def list_tools():
    current_user = _require_mcp_current_user()
    base_tools = [
        types.Tool(
            name="list_rpas",
            description=(
                "查看当前可用的 RPA 工具列表、能力标签和参数结构。\n"
                "适用场景：用户询问有哪些自动化能力，或者你需要先了解当前注册了哪些工具时。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "available_only": {
                        "type": "boolean",
                        "description": "是否只返回当前在线且可执行的工具。默认 true。",
                    },
                },
            },
        ),
    ]
    if _is_admin_user(current_user):
        base_tools.append(
            types.Tool(
                name="dispatch_rpa",
                description=(
                    "底层手动派发工具，用于把指定 RPA 任务下发到某台机器执行。\n"
                    "适用场景：你明确知道 rpa_id，且需要手动指定 machine_id 或直接走底层派发时。"
                ),
                inputSchema={
                    "type": "object",
                    "required": ["rpa_id", "params"],
                    "properties": {
                        "rpa_id": {"type": "string"},
                        "machine_id": {"type": "string"},
                        "params": {"type": "object"},
                    },
                },
            )
        )

    dynamic_tools = [
        types.Tool(
            name=rpa["id"],
            description=_build_registry_tool_description(rpa),
            inputSchema=rpa["params_schema"],
        )
        for rpa in _filter_rpas_for_current_user(await get_available_rpas(), current_user)
    ]
    logger.info(
        "MCP list_tools called for user=%s, returning %s tools",
        current_user.get("id"),
        len(base_tools) + len(dynamic_tools),
    )
    return base_tools + dynamic_tools


async def _perform_dispatch(
    rpa_id: str,
    params: dict,
    machine_id: str = None,
    conversation_id: Optional[str] = None,
    conversation_title: Optional[str] = None,
    owner_user_id: Optional[str] = None,
):
    normalized_machine_id = str(machine_id or "").strip() or None
    if not normalized_machine_id:
        machines = await get_online_machines_for_rpa(rpa_id)
        if not machines:
            logger.warning("No available machines for RPA %s", rpa_id)
            return _build_dispatch_error(f"No available machines for RPA {rpa_id}")
        normalized_machine_id = machines[0]
    else:
        machine_validation_error = await _validate_dispatch_machine(rpa_id, normalized_machine_id)
        if machine_validation_error:
            logger.warning(machine_validation_error["message"])
            return machine_validation_error

    logger.info("Dispatching RPA %s to machine %s", rpa_id, normalized_machine_id)
    result = await engine.dispatch(
        machine_id=normalized_machine_id,
        rpa_id=rpa_id,
        params=params,
        conversation_id=conversation_id,
        conversation_title=conversation_title,
        owner_user_id=owner_user_id,
    )
    logger.info("Dispatch result for %s: %s", rpa_id, result.get("status"))
    return result


async def _perform_dispatch_async(
    rpa_id: str,
    params: dict,
    machine_id: str = None,
    conversation_id: Optional[str] = None,
    conversation_title: Optional[str] = None,
    owner_user_id: Optional[str] = None,
):
    normalized_machine_id = str(machine_id or "").strip() or None
    if not normalized_machine_id:
        machines = await get_online_machines_for_rpa(rpa_id)
        if not machines:
            logger.warning("No available machines for async RPA %s", rpa_id)
            return _build_dispatch_error(f"No available machines for RPA {rpa_id}")
        normalized_machine_id = machines[0]
    else:
        machine_validation_error = await _validate_dispatch_machine(rpa_id, normalized_machine_id)
        if machine_validation_error:
            logger.warning(machine_validation_error["message"])
            return machine_validation_error

    logger.info("Dispatching async RPA %s to machine %s", rpa_id, normalized_machine_id)
    result = await engine.dispatch_async(
        machine_id=normalized_machine_id,
        rpa_id=rpa_id,
        params=params,
        conversation_id=conversation_id,
        conversation_title=conversation_title,
        owner_user_id=owner_user_id,
    )
    return result


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    current_user = _require_mcp_current_user()
    args_dict = getattr(arguments, "model_dump", lambda: arguments)() if hasattr(arguments, "model_dump") else arguments
    if not isinstance(args_dict, dict):
        args_dict = vars(args_dict) if hasattr(args_dict, "__dict__") else dict(args_dict)

    if name == "list_rpas":
        tags = args_dict.get("tags")
        available_only = args_dict.get("available_only", True)
        if available_only:
            rpas = await get_available_rpas(tags=tags)
        else:
            rpas = await get_all_rpas(tags=tags)
        filtered_rpas = _filter_rpas_for_current_user(rpas, current_user)
        return [types.TextContent(type="text", text=json.dumps(filtered_rpas, ensure_ascii=False))]

    if name == "dispatch_rpa":
        _require_admin_mcp_user(current_user)
        rpa_id = str(args_dict.get("rpa_id") or "").strip()
        if not rpa_id:
            raise Exception("Missing required parameter: rpa_id")
        result = await _perform_dispatch(
            rpa_id,
            args_dict.get("params", {}),
            args_dict.get("machine_id"),
            owner_user_id=current_user["id"],
        )
        if result.get("status") != "success":
            raise Exception(result.get("error") or result.get("message") or "Unknown RPA error")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    registered_rpa = await get_rpa(name)
    if registered_rpa:
        _require_rpa_access(current_user, name)

        rpa = await get_available_rpa(name)
        if not rpa:
            raise Exception(f"Tool currently unavailable: {name}")

        result = await _perform_dispatch(name, args_dict, owner_user_id=current_user["id"])
        if result.get("status") != "success":
            raise Exception(result.get("error") or result.get("message") or "Unknown RPA error")
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    raise Exception(f"Unknown tool: {name}")


@app.get("/task/{task_id}")
async def check_task(task_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    task = await get_task(task_id, **_build_owner_scope(current_user))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/tasks")
async def get_tasks(limit: int = 50, request: Request = None):
    current_user = await _resolve_request_user(request, require_user=True)
    return await list_tasks(limit=limit, **_build_owner_scope(current_user))


@app.delete("/task/{task_id}")
async def delete_task_api(task_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    task = await get_task(task_id, **_build_owner_scope(current_user))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    status = str(task.get("status") or "").lower()
    if status not in TERMINAL_TASK_STATUSES:
        raise HTTPException(status_code=409, detail="Running tasks cannot be deleted yet")

    deleted = await delete_task_record(task_id, **_build_owner_scope(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "success", "deleted_task_id": task_id}


@app.get("/machines")
async def get_machines(request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    machines = await list_machines()
    return _filter_machines_for_current_user(machines, current_user)


@app.delete("/machines/{machine_id}")
async def delete_machine_api(machine_id: str, request: Request):
    await _resolve_request_user(request, require_admin=True)
    machine = await get_machine(machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    if str(machine.get("status") or "").lower() == "online":
        raise HTTPException(status_code=409, detail="Online machines cannot be deleted")
    if int(machine.get("running_task_count") or 0) > 0:
        raise HTTPException(status_code=409, detail="Machines with running tasks cannot be deleted")

    deleted = await delete_machine_record(machine_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Machine not found")
    return {"status": "success", "deleted_machine_id": machine_id}


class AsyncDispatchRequest(BaseModel):
    rpa_id: str
    params: dict
    machine_id: Optional[str] = None
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    owner_user_id: Optional[str] = None


@app.post("/dispatch/sync")
async def dispatch_sync_api(data: AsyncDispatchRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    _require_http_rpa_access(current_user, data.rpa_id)
    resolved_owner_user_id = current_user["id"]
    if data.owner_user_id and data.owner_user_id != resolved_owner_user_id:
        logger.warning(
            "Ignoring client supplied owner_user_id=%s for sync dispatch user=%s",
            data.owner_user_id,
            resolved_owner_user_id,
        )
    result = await _perform_dispatch(
        data.rpa_id,
        data.params,
        data.machine_id,
        data.conversation_id,
        data.conversation_title,
        resolved_owner_user_id,
    )
    _raise_http_for_dispatch_result(result, "success")
    return result


@app.post("/dispatch/async")
async def dispatch_async_api(data: AsyncDispatchRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    _require_http_rpa_access(current_user, data.rpa_id)
    resolved_owner_user_id = current_user["id"]
    if data.owner_user_id and data.owner_user_id != resolved_owner_user_id:
        logger.warning(
            "Ignoring client supplied owner_user_id=%s for async dispatch user=%s",
            data.owner_user_id,
            resolved_owner_user_id,
        )
    result = await _perform_dispatch_async(
        data.rpa_id,
        data.params,
        data.machine_id,
        data.conversation_id,
        data.conversation_title,
        resolved_owner_user_id,
    )
    _raise_http_for_dispatch_result(result, "accepted")
    return result


class ConversationUpdate(BaseModel):
    id: str
    title: str


class MessageUpdate(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    timestamp: str
    meta: Optional[dict] = None


class CommonTaskCreateRequest(BaseModel):
    title: str
    prompt: str
    summary: Optional[str] = None
    plan: Optional[dict] = None


class TaskScheduleRequest(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    summary: Optional[str] = None
    common_task_id: Optional[str] = None
    provider: Optional[str] = None
    schedule_type: str
    schedule_config: Dict[str, Any]
    timezone: Optional[str] = None
    is_active: bool = True


class TaskScheduleToggleRequest(BaseModel):
    is_active: bool


class InternalSessionCreateRequest(BaseModel):
    user_id: str
    ttl_seconds: Optional[int] = None


class InternalSessionRevokeRequest(BaseModel):
    session_token: str


class InternalClaimSchedulesRequest(BaseModel):
    limit: int = 3


class InternalScheduleRunStartRequest(BaseModel):
    claim_token: str


class InternalScheduleRunCompleteRequest(BaseModel):
    claim_token: str
    status: str
    response_text: Optional[str] = None
    runtime_plan: Optional[dict] = None
    tool_trace: Optional[list] = None
    error_text: Optional[str] = None
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    provider: Optional[str] = None


async def _resolve_schedule_request_payload(data: TaskScheduleRequest, current_user: Dict[str, Any]) -> Dict[str, Any]:
    owner_scope = _build_owner_scope(current_user)
    common_task = None
    if data.common_task_id:
        common_task = await get_common_task(data.common_task_id, **owner_scope)
        if not common_task:
            raise HTTPException(status_code=404, detail="Common task not found")

    title = str(data.title or "").strip() or str((common_task or {}).get("title") or "").strip()
    prompt = str(data.prompt or "").strip() or str((common_task or {}).get("prompt") or "").strip()
    summary = str(data.summary or "").strip()
    if not summary and common_task:
        summary = str(common_task.get("summary") or "").strip()

    return {
        "title": title,
        "prompt": prompt,
        "summary": summary or None,
        "common_task_id": str(data.common_task_id or "").strip() or None,
        "provider": str(data.provider or "").strip() or None,
        "schedule_type": data.schedule_type,
        "schedule_config": data.schedule_config,
        "timezone_name": data.timezone,
        "is_active": bool(data.is_active),
        "owner_user_id": current_user["id"],
    }


@app.get("/history/conversations")
async def list_conversations(request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    return await get_conversations(**_build_owner_scope(current_user))


@app.get("/history/messages/{conversation_id}")
async def list_messages(conversation_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    return await get_conversation_messages(conversation_id, **_build_owner_scope(current_user))


@app.post("/history/conversations")
async def update_conversation(conv: ConversationUpdate, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    await save_conversation(conv.id, conv.title, **_build_owner_scope(current_user))
    return {"status": "success"}


@app.post("/history/messages")
async def update_message(msg: MessageUpdate, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    await save_message(
        msg.id,
        msg.conversation_id,
        msg.role,
        msg.content,
        msg.timestamp,
        msg.meta,
        **_build_owner_scope(current_user),
    )
    return {"status": "success"}


@app.delete("/history/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    await delete_conversation(conversation_id, **_build_owner_scope(current_user))
    return {"status": "success"}


@app.get("/common-tasks")
async def get_common_tasks(request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    return await list_common_tasks(**_build_owner_scope(current_user))


@app.post("/common-tasks")
async def create_common_task_api(data: CommonTaskCreateRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    try:
        common_task = await create_common_task(
            title=data.title,
            prompt=data.prompt,
            summary=data.summary,
            plan=data.plan,
            owner_user_id=current_user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return common_task


@app.post("/common-tasks/{task_id}/use")
async def use_common_task_api(task_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    common_task = await touch_common_task(task_id, **_build_owner_scope(current_user))
    if not common_task:
        raise HTTPException(status_code=404, detail="Common task not found")
    return common_task


@app.delete("/common-tasks/{task_id}")
async def delete_common_task_api(task_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    deleted = await delete_common_task(task_id, **_build_owner_scope(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Common task not found")
    return {"status": "success", "deleted_task_id": task_id}


@app.get("/schedules")
async def get_task_schedules_api(request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    return await list_task_schedules(**_build_owner_scope(current_user))


@app.post("/schedules")
async def create_task_schedule_api(data: TaskScheduleRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    payload = await _resolve_schedule_request_payload(data, current_user)
    try:
        return await create_task_schedule(**payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/schedules/{schedule_id}")
async def update_task_schedule_api(schedule_id: str, data: TaskScheduleRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    existing = await get_task_schedule(schedule_id, **_build_owner_scope(current_user))
    if not existing:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    payload = await _resolve_schedule_request_payload(data, current_user)
    payload["owner_user_id"] = existing.get("owner_user_id") or current_user["id"]
    try:
        updated = await update_task_schedule(schedule_id, include_all=_is_admin_user(current_user), **payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    return updated


@app.post("/schedules/{schedule_id}/toggle")
async def toggle_task_schedule_api(schedule_id: str, data: TaskScheduleToggleRequest, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    try:
        updated = await set_task_schedule_active(
            schedule_id,
            data.is_active,
            owner_user_id=current_user["id"],
            include_all=_is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    return updated


@app.delete("/schedules/{schedule_id}")
async def delete_task_schedule_api(schedule_id: str, request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    deleted = await delete_task_schedule(schedule_id, **_build_owner_scope(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Task schedule not found")
    return {"status": "success", "deleted_schedule_id": schedule_id}


@app.get("/schedule-runs")
async def list_task_schedule_runs_api(limit: int = 50, schedule_id: Optional[str] = None, request: Request = None):
    current_user = await _resolve_request_user(request, require_user=True)
    return await list_task_schedule_runs(
        limit=limit,
        schedule_id=schedule_id,
        **_build_owner_scope(current_user),
    )


@app.post("/internal/schedule-sessions")
async def create_internal_schedule_session_api(data: InternalSessionCreateRequest, request: Request):
    _require_internal_request(request)
    try:
        session_token = await create_internal_session_for_user(
            data.user_id,
            ttl_seconds=data.ttl_seconds if data.ttl_seconds else 3600,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_token": session_token}


@app.post("/internal/schedule-sessions/revoke")
async def revoke_internal_schedule_session_api(data: InternalSessionRevokeRequest, request: Request):
    _require_internal_request(request)
    await delete_session(data.session_token)
    return {"status": "success"}


@app.post("/internal/schedules/claim-due")
async def claim_due_task_schedules_api(data: InternalClaimSchedulesRequest, request: Request):
    _require_internal_request(request)
    try:
        items = await claim_due_task_schedules(limit=data.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items}


@app.post("/internal/schedules/{schedule_id}/runs/{run_id}/start")
async def start_task_schedule_run_api(
    schedule_id: str,
    run_id: str,
    data: InternalScheduleRunStartRequest,
    request: Request,
):
    _require_internal_request(request)
    try:
        run = await mark_task_schedule_run_started(schedule_id, run_id, data.claim_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run:
        raise HTTPException(status_code=404, detail="Task schedule run not found")
    return run


@app.post("/internal/schedules/{schedule_id}/runs/{run_id}/complete")
async def complete_task_schedule_run_api(
    schedule_id: str,
    run_id: str,
    data: InternalScheduleRunCompleteRequest,
    request: Request,
):
    _require_internal_request(request)
    try:
        run = await complete_task_schedule_run(
            schedule_id,
            run_id,
            data.claim_token,
            status=data.status,
            response_text=data.response_text,
            runtime_plan=data.runtime_plan,
            tool_trace=data.tool_trace,
            error_text=data.error_text,
            conversation_id=data.conversation_id,
            conversation_title=data.conversation_title,
            provider=data.provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run:
        raise HTTPException(status_code=404, detail="Task schedule run not found")
    return run


transport = SessionBoundSseTransport("/mcp/messages")


async def _disconnect_stale_machines_once():
    stale_machine_ids = await get_stale_online_machine_ids(HEARTBEAT_TIMEOUT_SEC)
    for machine_id in stale_machine_ids:
        logger.warning("Machine heartbeat timed out, disconnecting: %s", machine_id)
        await engine.disconnect(machine_id)


async def _heartbeat_monitor_loop():
    while True:
        try:
            await _disconnect_stale_machines_once()
        except Exception as exc:
            logger.error("Heartbeat monitor loop error: %s", exc)
        await asyncio.sleep(HEARTBEAT_SWEEP_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Initializing registry database...")
    await init_db()
    heartbeat_task = asyncio.create_task(_heartbeat_monitor_loop())
    try:
        yield
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        logger.info("Shutting down registry...")


app.router.lifespan_context = lifespan


class NoResponse(Response):
    async def __call__(self, scope, receive, send):
        return None


@app.get("/mcp/sse")
async def handle_sse(request: Request):
    current_user = await _resolve_request_user(request, require_user=True)
    session_token = _require_session_token(request)
    async with _bind_mcp_request_context(request, current_user, session_token) as streams:
        await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())
    return NoResponse()


@app.post("/mcp/messages")
async def handle_messages(request: Request):
    await _resolve_request_user(request, require_user=True)
    session_token = _require_session_token(request)
    await transport.handle_post_message(request.scope, request.receive, request._send, session_token=session_token)
    return NoResponse()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    logger.info("New WebSocket connection attempt from Agent")
    await ws.accept()
    machine_id = None
    try:
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            if msg["type"] == "register":
                if not _is_valid_agent_ws_token(msg.get("auth_token")):
                    logger.warning("Rejected Agent websocket registration due to invalid auth token")
                    await ws.close(code=1008, reason="Invalid agent websocket token")
                    return
                machine_id = msg["machine_id"]
                await engine.register(
                    machine_id,
                    ws,
                    msg.get("system_info", {}),
                    msg.get("rpas", []),
                    msg.get("manifests", []),
                )
            elif msg["type"] == "result":
                await engine.resolve(msg["task_id"], msg["result"])
            elif msg["type"] == "heartbeat" and machine_id:
                await update_machine_status(machine_id, "online")
    except WebSocketDisconnect:
        logger.info("Agent websocket disconnected: %s", machine_id)
        if machine_id:
            await engine.disconnect(machine_id)
    except Exception as exc:
        logger.error("Error in websocket: %s", exc)
        if machine_id:
            await engine.disconnect(machine_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("registry.main:app", host="127.0.0.1", port=8000, reload=False)
