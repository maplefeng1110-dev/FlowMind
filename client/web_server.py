import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from client.agent_admin_token_store import get_agent_admin_token_store_health, load_agent_admin_token_store
from client.ai_manager import ai_manager
from client.export_center import build_export_bundle
from client.schedule_runtime import scheduler_enabled, scheduler_loop
from client.web_auth import (
    SESSION_COOKIE_NAME,
    apply_login_cookie,
    clear_login_cookie,
    filter_rpas_for_user,
    get_session_token_from_request,
    require_admin_user,
    require_current_user,
    serialize_user,
)
from client.web_server_logic import (
    _generate_background_task_summary,
    _proxy_json,
    chat_with_mcp,
    logger,
    stream_chat_with_mcp,
)
from client.web_server_models import (
    ChatRequest,
    CommonTaskCreateRequest,
    CreateUserRequest,
    ExportDownloadRequest,
    LoginRequest,
    ScaffoldPluginRequest,
    TaskScheduleRequest,
    TaskScheduleToggleRequest,
    TaskSummaryRequest,
    UpdateUserPluginsRequest,
)
from registry.db import (
    authenticate_user,
    create_session,
    create_user,
    delete_session,
    get_all_rpas,
    get_available_rpas,
    init_db,
    list_users,
    set_user_allowed_rpas,
    upsert_rpa_manifests,
)
from utils.paths import UPLOAD_DIR, ensure_runtime_dirs

app = FastAPI(title="RPA Orchestrator Web UI")
WEB_PATH = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_runtime_dirs()
    await init_db()
    schedule_task = None
    try:
        token_store_health = get_agent_admin_token_store_health()
        if token_store_health["exists"]:
            logger.info(
                "Agent Admin token store loaded from %s with %s machine tokens",
                token_store_health["path"],
                len(token_store_health["machine_tokens"]),
            )
        for warning in token_store_health["warnings"]:
            logger.warning("Agent Admin token store: %s", warning)
    except ValueError as exc:
        logger.error("Agent Admin token store invalid: %s", exc)
    if scheduler_enabled():
        schedule_task = asyncio.create_task(scheduler_loop())
    else:
        logger.info("Schedule runtime disabled by FLOWMIND_SCHEDULER_ENABLED")
    yield
    if schedule_task:
        schedule_task.cancel()
        with suppress(asyncio.CancelledError):
            await schedule_task


app.router.lifespan_context = lifespan


async def _resolve_route_user(http_request: Optional[Request]):
    if http_request is None:
        return None
    return await require_current_user(http_request)


async def _resolve_admin_route_user(http_request: Optional[Request]):
    if http_request is None:
        raise HTTPException(status_code=500, detail="admin route requires http request context")
    return await require_admin_user(http_request)


async def _proxy_registry_request(
    http_request: Optional[Request],
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
):
    return await _proxy_json(
        method,
        path,
        json_body=json_body,
        params=params,
        session_token=get_session_token_from_request(http_request),
    )


@app.middleware("http")
async def disable_cache_for_web_assets(request: Request, call_next):
    """禁止首页和静态资源缓存，避免前端脚本老版本残留。"""

    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _safe_upload_name(filename: str) -> str:
    """生成安全的上传文件名，避免特殊字符和重名冲突。"""

    base_name = Path(filename or "upload.bin").name
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in base_name)
    return f"{int(time.time())}_{sanitized or 'upload.bin'}"


def _build_conversation_context(conversation_id: str | None, conversation_title: str | None):
    """把当前对话信息整理成后台任务可复用的上下文。"""

    if not conversation_id:
        return None
    return {
        "conversation_id": conversation_id,
        "conversation_title": conversation_title or "未命名对话",
    }


@app.get("/")
async def index():
    """返回前端首页。"""

    return FileResponse(WEB_PATH / "index.html")


@app.post("/api/auth/login")
async def login(data: LoginRequest, response: Response, http_request: Request):
    user = await authenticate_user(data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    session_token = await create_session(user["id"])
    apply_login_cookie(response, session_token, http_request)
    return {"user": serialize_user(user)}


@app.post("/api/auth/logout")
async def logout(response: Response, http_request: Request = None):
    if http_request is not None:
        session_token = http_request.cookies.get(SESSION_COOKIE_NAME)
        if session_token:
            await delete_session(session_token)
    clear_login_cookie(response)
    return {"status": "success"}


@app.get("/api/auth/me")
async def get_current_user_profile(http_request: Request):
    user = await require_current_user(http_request)
    return {"user": serialize_user(user)}


@app.get("/api/admin/users")
async def admin_list_users(http_request: Request):
    await _resolve_admin_route_user(http_request)
    return {"users": [serialize_user(user) for user in await list_users(include_inactive=True)]}


@app.get("/api/admin/plugins")
async def admin_list_plugins(http_request: Request):
    await _resolve_admin_route_user(http_request)
    return {"plugins": await get_all_rpas(include_machine_info=True)}


@app.get("/api/admin/agent-admin-token-store")
async def admin_get_agent_admin_token_store(http_request: Request):
    await _resolve_admin_route_user(http_request)
    try:
        return get_agent_admin_token_store_health()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/admin/users")
async def admin_create_user(data: CreateUserRequest, http_request: Request):
    await _resolve_admin_route_user(http_request)
    try:
        user = await create_user(
            username=data.username,
            password=data.password,
            role=data.role,
            display_name=data.display_name,
        )
        user = await set_user_allowed_rpas(user["id"], data.allowed_rpa_ids)
        return {"user": serialize_user(user)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/users/{user_id}/plugins")
async def admin_update_user_plugins(user_id: str, data: UpdateUserPluginsRequest, http_request: Request):
    await _resolve_admin_route_user(http_request)
    try:
        user = await set_user_allowed_rpas(user_id, data.allowed_rpa_ids)
        return {"user": serialize_user(user)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _relative_or_absolute_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _load_local_agent_scaffold_plugin():
    try:
        from agent.plugin_scaffold import scaffold_plugin
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "当前部署未提供本地 Agent 脚手架能力。"
                "请在 Agent 节点执行 `python -m agent.scaffold_plugin ...` 完成插件生成。"
            ),
        ) from exc
    return scaffold_plugin


def _get_agent_admin_api_url() -> str:
    return str(os.getenv("AGENT_ADMIN_API_URL", "") or "").strip().rstrip("/")


def _get_agent_admin_api_token() -> str:
    return str(os.getenv("AGENT_ADMIN_API_TOKEN", "") or "").strip()


def _get_agent_admin_api_tokens_json() -> str:
    return str(os.getenv("AGENT_ADMIN_API_TOKENS_JSON", "") or "").strip()


def _load_agent_admin_token_store_config() -> dict:
    try:
        return load_agent_admin_token_store()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _get_agent_admin_api_token_map() -> dict[str, str]:
    token_map = dict(_load_agent_admin_token_store_config().get("machine_tokens") or {})
    raw_value = _get_agent_admin_api_tokens_json()
    if not raw_value:
        return token_map

    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail="AGENT_ADMIN_API_TOKENS_JSON 不是合法 JSON，请检查 Web 服务配置。",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail="AGENT_ADMIN_API_TOKENS_JSON 必须是 {machine_id: token} 对象。",
        )

    for machine_id, token in payload.items():
        normalized_machine_id = str(machine_id or "").strip()
        normalized_token = str(token or "").strip()
        if normalized_machine_id and normalized_token and normalized_machine_id not in token_map:
            token_map[normalized_machine_id] = normalized_token
    return token_map


def _resolve_agent_admin_api_token(machine_id: str | None = None) -> str:
    normalized_machine_id = str(machine_id or "").strip()
    if normalized_machine_id:
        token = _get_agent_admin_api_token_map().get(normalized_machine_id)
        if token:
            return token
    store_default_token = str(_load_agent_admin_token_store_config().get("default_token") or "").strip()
    if store_default_token:
        return store_default_token
    return _get_agent_admin_api_token()


def _model_dump(data, *, exclude: set[str] | None = None) -> dict:
    exclude = exclude or set()
    if hasattr(data, "model_dump"):
        return data.model_dump(exclude_none=True, exclude=exclude)
    return data.dict(exclude_none=True, exclude=exclude)


def _extract_machine_agent_admin_api_url(machine: dict) -> str:
    system_info = machine.get("system_info")
    if not isinstance(system_info, dict):
        return ""
    agent_admin = system_info.get("agent_admin")
    if not isinstance(agent_admin, dict):
        return ""
    return str(agent_admin.get("api_url") or "").strip().rstrip("/")


async def _resolve_scaffold_target_agent_api_url(http_request: Optional[Request], machine_id: str | None) -> str:
    normalized_machine_id = str(machine_id or "").strip()
    if normalized_machine_id:
        machines = await _proxy_registry_request(http_request, "GET", "/machines")
        if not isinstance(machines, list):
            raise HTTPException(status_code=502, detail="Registry 返回了无效的机器列表。")

        target_machine = next((machine for machine in machines if machine.get("id") == normalized_machine_id), None)
        if not target_machine:
            raise HTTPException(status_code=404, detail=f"目标机器不存在：{normalized_machine_id}")
        if target_machine.get("status") != "online":
            raise HTTPException(status_code=409, detail=f"目标机器当前不在线：{normalized_machine_id}")

        api_url = _extract_machine_agent_admin_api_url(target_machine)
        if not api_url:
            raise HTTPException(
                status_code=409,
                detail="目标机器未暴露 Agent 管理 API，请先为该 Agent 配置 AGENT_ADMIN_PUBLIC_URL。",
            )
        return api_url

    return _get_agent_admin_api_url()


async def _scaffold_plugin_via_remote_agent_api(data: ScaffoldPluginRequest, base_url: str | None = None) -> dict:
    base_url = str(base_url or _get_agent_admin_api_url() or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail="AGENT_ADMIN_API_URL 未配置。")

    headers = {}
    token = _resolve_agent_admin_api_token(data.machine_id)
    if token:
        headers["X-FlowMind-Agent-Admin-Token"] = token

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/api/plugins/scaffold",
                json=_model_dump(data, exclude={"machine_id"}),
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Agent 管理 API 不可达：{exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=payload.get("detail") or "远端 Agent 管理 API 调用失败。",
        )

    if not isinstance(payload, dict) or not payload.get("plugin"):
        raise HTTPException(status_code=502, detail="远端 Agent 管理 API 返回了无效响应。")
    return payload


@app.post("/api/admin/plugins/scaffold")
async def admin_scaffold_plugin(data: ScaffoldPluginRequest, http_request: Request):
    await _resolve_admin_route_user(http_request)

    try:
        remote_agent_admin_url = await _resolve_scaffold_target_agent_api_url(http_request, data.machine_id)
        if remote_agent_admin_url:
            result = await _scaffold_plugin_via_remote_agent_api(data, remote_agent_admin_url)
            manifest = result.get("plugin") or {}
            await upsert_rpa_manifests([manifest])
            return result

        argv = [data.plugin_id]
        if data.description:
            argv.extend(["--description", data.description])
        if data.source_file:
            argv.extend(["--source-file", data.source_file])
        for item in data.tags:
            argv.extend(["--tag", item])
        for item in data.capabilities:
            argv.extend(["--capability", item])
        for item in data.keywords:
            argv.extend(["--keyword", item])
        if not data.source_file:
            for item in data.params:
                argv.extend(["--param", item])
            for item in data.required_params:
                argv.extend(["--required", item])
        argv.extend(["--timeout-sec", str(data.timeout_sec)])
        if data.force:
            argv.append("--force")

        plugin_dir = _load_local_agent_scaffold_plugin()(argv)
        manifest = yaml.safe_load((plugin_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
        await upsert_rpa_manifests([manifest])
    except (FileNotFoundError, FileExistsError, ValueError, SyntaxError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"本地 Agent 插件脚手架写入失败：{exc}") from exc

    files = ["manifest.yaml", "__init__.py"]
    if (plugin_dir / "impl.py").exists():
        files.append("impl.py")

    return {
        "plugin": manifest,
        "plugin_dir": _relative_or_absolute_path(plugin_dir),
        "files": files,
        "message": "本地 Agent 工作区的插件脚手架已生成，manifest 已写入 Registry 元数据。重启对应 Agent 后即可完成注册并上线。",
        "restart_required": True,
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), http_request: Request = None):
    """接收用户上传文件，并保存到运行时上传目录。"""

    await _resolve_route_user(http_request)
    try:
        ensure_runtime_dirs()
        file_path = UPLOAD_DIR / _safe_upload_name(file.filename)
        content = await file.read()
        file_path.write_bytes(content)

        logger.info("File uploaded: %s -> %s", file.filename, file_path)
        return {
            "filename": file.filename,
            "path": str(file_path.relative_to(Path.cwd())) if file_path.is_relative_to(Path.cwd()) else str(file_path),
            "size": len(content),
        }
    except Exception as exc:
        logger.error("Upload error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save uploaded file") from exc


if (WEB_PATH / "static").exists():
    app.mount("/static", StaticFiles(directory=str(WEB_PATH / "static")), name="static")


@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request = None):
    """非流式聊天接口，返回本轮最终回复。"""

    current_user = await _resolve_route_user(http_request)
    provider = request.provider or ai_manager.get_preferred_provider()
    logger.info("POST /api/chat - Provider: %s", provider)

    if not provider:
        return {"response": "Error: No AI provider configured. Please set API keys in .env"}

    messages = [{"role": msg["role"], "content": msg.get("content", "")} for msg in request.messages]
    conversation_context = _build_conversation_context(request.conversation_id, request.conversation_title)
    session_token = get_session_token_from_request(http_request)

    try:
        return await chat_with_mcp(
            messages,
            provider,
            conversation_context=conversation_context,
            allowed_rpa_ids=current_user.allowed_rpa_ids if current_user else None,
            session_token=session_token,
        )
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return {"response": f"Error calling {provider}: {exc}", "provider": provider}


@app.get("/api/tools")
async def get_tools(http_request: Request = None):
    """返回前端可用工具弹窗所需的插件列表。"""

    current_user = await _resolve_route_user(http_request)
    try:
        tools = filter_rpas_for_user(await get_available_rpas(), current_user)
        return {"tools": tools}
    except Exception as exc:
        logger.error("Failed to fetch tools: %s", exc)
        return {"tools": [], "error": str(exc)}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request = None):
    """流式聊天接口，持续返回正文和工具调用轨迹。"""

    current_user = await _resolve_route_user(http_request)
    provider = request.provider or ai_manager.get_preferred_provider()
    logger.info("POST /api/chat/stream - Provider: %s", provider)

    if not provider:
        return {"response": "Error: No AI provider configured"}

    messages = [{"role": msg["role"], "content": msg.get("content", "")} for msg in request.messages]
    conversation_context = _build_conversation_context(request.conversation_id, request.conversation_title)
    session_token = get_session_token_from_request(http_request)
    stream = await stream_chat_with_mcp(
        messages,
        provider,
        conversation_context,
        allowed_rpa_ids=current_user.allowed_rpa_ids if current_user else None,
        session_token=session_token,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


@app.get("/api/task/{task_id}")
async def proxy_task(task_id: str, http_request: Request = None):
    """查询单个后台任务状态，并按账号归属过滤。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", f"/task/{task_id}")


@app.get("/api/tasks")
async def proxy_tasks(limit: int = 30, http_request: Request = None):
    """查询任务中心的任务列表。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", "/tasks", params={"limit": limit})


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str, http_request: Request = None):
    """删除已结束的后台任务。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "DELETE", f"/task/{task_id}")


@app.get("/api/machines")
async def proxy_machines(http_request: Request = None):
    """查询机器面板所需的机器列表。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", "/machines")


@app.delete("/api/machines/{machine_id}")
async def delete_machine(machine_id: str, http_request: Request = None):
    """删除离线机器记录。"""

    await _resolve_admin_route_user(http_request)
    return await _proxy_registry_request(http_request, "DELETE", f"/machines/{machine_id}")


@app.post("/api/task-summary")
async def summarize_task_result(request: TaskSummaryRequest, http_request: Request = None):
    """把后台任务结果总结成更适合聊天展示的文本。"""

    await _resolve_route_user(http_request)
    summary = await _generate_background_task_summary(
        tool_name=request.tool_name,
        task=request.task,
        arguments=request.arguments,
    )
    return {"summary": summary}


@app.get("/api/history/conversations")
async def list_history_conversations(http_request: Request = None):
    """查询当前账号可见的历史对话列表。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", "/history/conversations")


@app.get("/api/history/messages/{conversation_id}")
async def list_history_messages(conversation_id: str, http_request: Request = None):
    """查询指定对话的消息列表。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", f"/history/messages/{conversation_id}")


@app.post("/api/history/conversations")
async def update_conversation(data: dict, http_request: Request = None):
    """保存或更新对话元信息。"""

    await _resolve_route_user(http_request)
    if "id" not in data or "title" not in data:
        raise HTTPException(status_code=400, detail="missing field: id/title")
    return await _proxy_registry_request(http_request, "POST", "/history/conversations", json_body=data)


@app.post("/api/history/messages")
async def update_message(data: dict, http_request: Request = None):
    """保存或更新消息内容。"""

    await _resolve_route_user(http_request)
    required_fields = {"id", "conversation_id", "role", "content", "timestamp"}
    missing_fields = sorted(required_fields - set(data))
    if missing_fields:
        raise HTTPException(status_code=400, detail=f"missing field: {', '.join(missing_fields)}")
    return await _proxy_registry_request(http_request, "POST", "/history/messages", json_body=data)


@app.delete("/api/history/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str, http_request: Request = None):
    """删除一条历史对话。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "DELETE", f"/history/conversations/{conversation_id}")


@app.get("/api/common-tasks")
async def list_common_tasks(http_request: Request = None):
    """读取当前账号的常用任务模板。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", "/common-tasks")


@app.post("/api/common-tasks")
async def create_common_task(data: CommonTaskCreateRequest, http_request: Request = None):
    """保存当前账号的常用任务模板。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "POST", "/common-tasks", json_body=_model_dump(data))


@app.post("/api/common-tasks/{task_id}/use")
async def use_common_task(task_id: str, http_request: Request = None):
    """标记常用任务已复用。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "POST", f"/common-tasks/{task_id}/use")


@app.delete("/api/common-tasks/{task_id}")
async def delete_common_task(task_id: str, http_request: Request = None):
    """删除一条常用任务模板。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "DELETE", f"/common-tasks/{task_id}")


@app.get("/api/schedules")
async def list_schedules(http_request: Request = None):
    """读取当前账号的定时调度计划。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "GET", "/schedules")


@app.post("/api/schedules")
async def create_schedule(data: TaskScheduleRequest, http_request: Request = None):
    """创建一条定时调度计划。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "POST", "/schedules", json_body=_model_dump(data))


@app.put("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, data: TaskScheduleRequest, http_request: Request = None):
    """更新一条定时调度计划。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "PUT", f"/schedules/{schedule_id}", json_body=_model_dump(data))


@app.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str, data: TaskScheduleToggleRequest, http_request: Request = None):
    """启用或暂停一条定时调度计划。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(
        http_request,
        "POST",
        f"/schedules/{schedule_id}/toggle",
        json_body=_model_dump(data),
    )


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, http_request: Request = None):
    """删除一条定时调度计划。"""

    await _resolve_route_user(http_request)
    return await _proxy_registry_request(http_request, "DELETE", f"/schedules/{schedule_id}")


@app.get("/api/schedule-runs")
async def list_schedule_runs(limit: int = 50, schedule_id: str | None = None, http_request: Request = None):
    """读取调度执行记录。"""

    await _resolve_route_user(http_request)
    params = {"limit": limit}
    if schedule_id:
        params["schedule_id"] = schedule_id
    return await _proxy_registry_request(http_request, "GET", "/schedule-runs", params=params)


async def _fetch_export_resource_payload(
    http_request: Request,
    resource: str,
    limit: int,
    conversation_id: str | None = None,
):
    normalized_resource = str(resource or "").strip().lower()
    if normalized_resource == "tasks":
        return await _proxy_registry_request(http_request, "GET", "/tasks", params={"limit": limit}), {}
    if normalized_resource == "common_tasks":
        return await _proxy_registry_request(http_request, "GET", "/common-tasks"), {}
    if normalized_resource == "schedules":
        return await _proxy_registry_request(http_request, "GET", "/schedules"), {}
    if normalized_resource == "schedule_runs":
        return await _proxy_registry_request(http_request, "GET", "/schedule-runs", params={"limit": limit}), {}
    if normalized_resource == "conversation":
        if not conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id is required for conversation export")
        conversations = await _proxy_registry_request(http_request, "GET", "/history/conversations")
        title = conversation_id
        if isinstance(conversations, list):
            matched = next((item for item in conversations if item.get("id") == conversation_id), None)
            if matched and matched.get("title"):
                title = matched["title"]
        messages = await _proxy_registry_request(http_request, "GET", f"/history/messages/{conversation_id}")
        return messages, {"conversation_id": conversation_id, "conversation_title": title}
    raise HTTPException(status_code=400, detail="unsupported export resource")


@app.post("/api/exports/download")
async def download_export(data: ExportDownloadRequest, http_request: Request):
    """统一导出中心：把任务、常用任务、调度计划和对话导出成标准下载格式。"""

    await _resolve_route_user(http_request)
    items, extra = await _fetch_export_resource_payload(
        http_request,
        data.resource,
        int(data.limit or 200),
        conversation_id=data.conversation_id,
    )
    try:
        content, media_type, filename = build_export_bundle(
            data.resource,
            data.format,
            items=items,
            conversation_id=extra.get("conversation_id"),
            conversation_title=extra.get("conversation_title"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=content, media_type=media_type, headers=headers)


@app.get("/api/provider")
async def get_provider(http_request: Request = None):
    """返回当前可用模型提供商和默认提供商。"""

    await _resolve_route_user(http_request)
    return {
        "providers": ai_manager.get_available_providers(),
        "current": ai_manager.get_preferred_provider(),
    }


if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 60)
    logger.info("RPA Orchestrator Web UI")
    logger.info("=" * 60)
    logger.info("Web UI available at: http://localhost:5173")
    logger.info("Current Provider: %s", ai_manager.get_preferred_provider())
    logger.info("=" * 60)
    uvicorn.run("client.web_server:app", host="127.0.0.1", port=5173, reload=False)
