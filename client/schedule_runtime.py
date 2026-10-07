import asyncio
import json
import os
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from client.ai_manager import ai_manager
from client.orchestration_runtime import register_task_update
from client.web_server_chat import chat_with_mcp
from client.web_server_tooling import _proxy_json, _summarize_tool_output, logger

SCHEDULER_POLL_SECONDS = max(5, int(os.getenv("FLOWMIND_SCHEDULER_POLL_SECONDS", "20")))
SCHEDULER_CLAIM_BATCH_SIZE = max(1, int(os.getenv("FLOWMIND_SCHEDULER_CLAIM_BATCH_SIZE", "3")))
SCHEDULER_SESSION_TTL_SECONDS = max(600, int(os.getenv("FLOWMIND_SCHEDULER_SESSION_TTL_SECONDS", "7200")))
# RPA tools are dispatched in the background; wait this long for them to finish before
# judging a run. Keep it well below the Registry's schedule claim TTL (900s default).
SCHEDULER_TASK_WAIT_SECONDS = max(0, int(os.getenv("FLOWMIND_SCHEDULER_TASK_WAIT_SECONDS", "180")))
SCHEDULER_TASK_POLL_SECONDS = 3.0
TERMINAL_TASK_STATUSES = {"success", "error", "timeout", "failed"}


def scheduler_enabled() -> bool:
    raw_value = str(os.getenv("FLOWMIND_SCHEDULER_ENABLED", "true") or "").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _chat_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _build_schedule_conversation_title(schedule: Dict[str, Any]) -> str:
    title = str(schedule.get("title") or "定时任务").strip() or "定时任务"
    return f"定时任务｜{title}"


def _build_schedule_message_meta(schedule: Dict[str, Any], run: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "schedule": {
            "schedule_id": schedule.get("id"),
            "run_id": run.get("id"),
            "title": schedule.get("title"),
            "schedule_type": schedule.get("schedule_type"),
            "planned_for": run.get("planned_for"),
        }
    }
    if extra:
        payload.update(extra)
    return payload


def _collect_tool_trace_statuses(tool_trace: Any) -> List[str]:
    if not isinstance(tool_trace, list):
        return []
    return [str(item.get("status") or "").strip().lower() for item in tool_trace if isinstance(item, dict)]


def _classify_schedule_result(result: Dict[str, Any]) -> Dict[str, Any]:
    runtime_plan = result.get("runtime_plan") if isinstance(result.get("runtime_plan"), dict) else None
    tool_trace = result.get("tool_trace") if isinstance(result.get("tool_trace"), list) else []
    response_text = str(result.get("response") or "").strip()
    provider = str(result.get("provider") or "").strip() or None

    plan_status = str((runtime_plan or {}).get("status") or "").strip().lower()
    tool_statuses = set(_collect_tool_trace_statuses(tool_trace))
    error_text = ""

    if any(status in {"error", "timeout", "failed"} for status in tool_statuses):
        error_text = response_text or "工具执行存在失败步骤，需要人工关注。"
        status = "needs_attention"
    elif "pending" in tool_statuses:
        error_text = "后台任务在等待时间内未完成，执行结果尚未确认，需要人工关注。"
        status = "needs_attention"
    elif plan_status == "needs_attention":
        recovery = (runtime_plan or {}).get("recovery") if isinstance((runtime_plan or {}).get("recovery"), dict) else {}
        error_text = str(recovery.get("suggested_action") or response_text or "运行时计划进入待处理状态。").strip()
        status = "needs_attention"
    elif not response_text and not tool_trace:
        error_text = "计划执行完成，但没有生成可用回复。"
        status = "error"
    elif response_text.startswith("Error") or "工具系统当前不可用" in response_text:
        error_text = response_text
        status = "error"
    else:
        status = "success"

    return {
        "status": status,
        "error_text": error_text or None,
        "response_text": response_text or None,
        "runtime_plan": runtime_plan,
        "tool_trace": tool_trace,
        "provider": provider,
    }


async def _create_schedule_session(user_id: str) -> str:
    payload = await _proxy_json(
        "POST",
        "/internal/schedule-sessions",
        json_body={"user_id": user_id, "ttl_seconds": SCHEDULER_SESSION_TTL_SECONDS},
    )
    session_token = str(payload.get("session_token") or "").strip()
    if not session_token:
        raise RuntimeError("Registry did not return a schedule session token")
    return session_token


async def _revoke_schedule_session(session_token: str) -> None:
    if not session_token:
        return
    try:
        await _proxy_json(
            "POST",
            "/internal/schedule-sessions/revoke",
            json_body={"session_token": session_token},
        )
    except Exception as exc:
        logger.warning("Failed to revoke schedule session: %s", exc)


async def _mark_schedule_run_started(schedule_id: str, run_id: str, claim_token: str) -> None:
    await _proxy_json(
        "POST",
        f"/internal/schedules/{schedule_id}/runs/{run_id}/start",
        json_body={"claim_token": claim_token},
    )


async def _await_background_tasks(
    result: Dict[str, Any],
    session_token: str,
    *,
    timeout_seconds: float = SCHEDULER_TASK_WAIT_SECONDS,
    poll_seconds: float = SCHEDULER_TASK_POLL_SECONDS,
) -> Dict[str, Any]:
    """Wait (bounded) for the tasks this run dispatched and fold their real outcome
    into tool_trace / runtime_plan, so the run is not judged on "pending"."""
    tool_trace = result.get("tool_trace") if isinstance(result.get("tool_trace"), list) else []
    runtime_plan = result.get("runtime_plan")
    pending = {
        str(item["task_id"]): item
        for item in tool_trace
        if isinstance(item, dict) and item.get("status") == "pending" and item.get("task_id")
    }
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while pending:
        for task_id in list(pending):
            try:
                task = await _proxy_json("GET", f"/task/{task_id}", session_token=session_token)
            except Exception as exc:  # noqa: BLE001 - retry until the deadline
                logger.warning("Failed to poll scheduled task %s: %s", task_id, exc)
                continue
            task_status = str((task or {}).get("status") or "").strip().lower()
            if task_status not in TERMINAL_TASK_STATUSES:
                continue
            item = pending.pop(task_id)
            item["status"] = "error" if task_status == "failed" else task_status
            item["result_preview"] = _summarize_tool_output(
                json.dumps((task or {}).get("result"), ensure_ascii=False, default=str)
            )
            runtime_plan = register_task_update(runtime_plan, task_id, item["status"], item["result_preview"])
        if not pending or time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_seconds)
    return {**result, "tool_trace": tool_trace, "runtime_plan": runtime_plan}


async def _complete_schedule_run(
    schedule_id: str,
    run_id: str,
    claim_token: str,
    payload: Dict[str, Any],
) -> None:
    await _proxy_json(
        "POST",
        f"/internal/schedules/{schedule_id}/runs/{run_id}/complete",
        json_body={"claim_token": claim_token, **payload},
    )


async def execute_claimed_schedule(item: Dict[str, Any]) -> None:
    schedule = item.get("schedule") if isinstance(item.get("schedule"), dict) else {}
    run = item.get("run") if isinstance(item.get("run"), dict) else {}
    claim_token = str(item.get("claim_token") or "").strip()
    schedule_id = str(schedule.get("id") or "").strip()
    run_id = str(run.get("id") or "").strip()
    owner_user_id = str(schedule.get("owner_user_id") or "").strip()
    if not schedule_id or not run_id or not claim_token or not owner_user_id:
        logger.warning("Skipping malformed claimed schedule item: %s", item)
        return

    session_token = ""
    try:
        provider = str(schedule.get("provider") or "").strip() or ai_manager.get_preferred_provider()
        if not provider:
            raise RuntimeError("No AI provider configured for scheduled execution")

        session_token = await _create_schedule_session(owner_user_id)
        await _mark_schedule_run_started(schedule_id, run_id, claim_token)

        conversation_id = str(uuid.uuid4())
        conversation_title = _build_schedule_conversation_title(schedule)
        prompt = str(schedule.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("Scheduled task prompt is empty")

        user_message = {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "role": "user",
            "content": prompt,
            "timestamp": _chat_timestamp(),
            "meta": _build_schedule_message_meta(schedule, run),
        }
        assistant_message_id = str(uuid.uuid4())

        await _proxy_json(
            "POST",
            "/history/conversations",
            json_body={"id": conversation_id, "title": conversation_title},
            session_token=session_token,
        )
        await _proxy_json(
            "POST",
            "/history/messages",
            json_body=user_message,
            session_token=session_token,
        )

        result = await chat_with_mcp(
            [{"role": "user", "content": prompt}],
            provider,
            conversation_context={
                "conversation_id": conversation_id,
                "conversation_title": conversation_title,
            },
            session_token=session_token,
        )
        if isinstance(result, dict):
            result = await _await_background_tasks(result, session_token)
        classified = _classify_schedule_result(result if isinstance(result, dict) else {})
        assistant_message = {
            "id": assistant_message_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": classified["response_text"] or "",
            "timestamp": _chat_timestamp(),
            "meta": _build_schedule_message_meta(
                schedule,
                run,
                {
                    "runtime_plan": classified["runtime_plan"],
                    "tool_trace": classified["tool_trace"],
                },
            ),
        }
        await _proxy_json(
            "POST",
            "/history/messages",
            json_body=assistant_message,
            session_token=session_token,
        )
        await _complete_schedule_run(
            schedule_id,
            run_id,
            claim_token,
            {
                "status": classified["status"],
                "response_text": classified["response_text"],
                "runtime_plan": classified["runtime_plan"],
                "tool_trace": classified["tool_trace"],
                "error_text": classified["error_text"],
                "conversation_id": conversation_id,
                "conversation_title": conversation_title,
                "provider": classified["provider"] or provider,
            },
        )
    except Exception as exc:
        logger.error("Scheduled run failed for schedule=%s run=%s: %s", schedule_id, run_id, exc)
        with suppress(Exception):
            await _complete_schedule_run(
                schedule_id,
                run_id,
                claim_token,
                {
                    "status": "error",
                    "error_text": str(exc),
                    "provider": str(schedule.get("provider") or "").strip() or None,
                },
            )
    finally:
        await _revoke_schedule_session(session_token)


async def process_due_schedules_once() -> int:
    payload = await _proxy_json(
        "POST",
        "/internal/schedules/claim-due",
        json_body={"limit": SCHEDULER_CLAIM_BATCH_SIZE},
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    claimed_items = items if isinstance(items, list) else []
    for item in claimed_items:
        await execute_claimed_schedule(item)
    return len(claimed_items)


async def scheduler_loop() -> None:
    logger.info("Schedule runtime loop started")
    while True:
        try:
            claimed_count = await process_due_schedules_once()
            if claimed_count:
                logger.info("Processed %s claimed schedules in this tick", claimed_count)
        except Exception as exc:
            logger.error("Schedule runtime loop error: %s", exc)
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)
