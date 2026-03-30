"""运行时编排基础设施：计划、恢复状态与常用任务建议。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from client.web_server_tooling import MAX_TOOL_CALL_STEPS, _extract_latest_user_text, _normalize_tool_profile


AUTOMATION_INTENT_TERMS = (
    "识别",
    "提取",
    "汇总",
    "写入",
    "生成",
    "发送",
    "发邮件",
    "邮件",
    "ocr",
    "excel",
    "word",
    "网页",
    "抓取",
    "查询",
    "处理",
)


def _clone(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return deepcopy(plan) if isinstance(plan, dict) else {}


def _normalize_steps(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = plan.get("steps")
    if not isinstance(steps, list):
        steps = []
    plan["steps"] = steps
    return steps


def _normalize_constraints(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    constraints = plan.get("constraints")
    if not isinstance(constraints, list):
        constraints = []
    plan["constraints"] = constraints
    return constraints


def _shorten(text: str, limit: int = 140) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _build_step_from_rpa(rpa: Dict[str, Any], index: int) -> Dict[str, Any]:
    profile = _normalize_tool_profile(rpa)
    return {
        "id": f"step-{index}",
        "tool_name": rpa.get("id"),
        "title": profile["solves"][0] if profile["solves"] else str(rpa.get("id") or f"步骤 {index}"),
        "status": "planned",
        "required_params": list((rpa.get("params_schema") or {}).get("required") or []),
        "outputs": profile["outputs"],
        "supports_batch": profile["supports_batch"],
        "has_side_effects": profile["has_side_effects"],
        "requires_confirmation": profile["requires_confirmation"],
        "can_resume": True,
        "attempts": 0,
        "max_retries": profile["max_retries"],
        "task_id": None,
        "arguments": {},
        "result_preview": "",
        "failure_category": "",
    }


def should_enable_runtime_plan(messages: List[Dict[str, Any]], required_rpas: List[Dict[str, Any]]) -> bool:
    if required_rpas:
        return True
    latest_user_text = _extract_latest_user_text(messages).lower()
    if "[attached file:" in latest_user_text:
        return True
    return any(term in latest_user_text for term in AUTOMATION_INTENT_TERMS)


def build_initial_runtime_plan(
    messages: List[Dict[str, Any]],
    required_rpas: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not should_enable_runtime_plan(messages, required_rpas):
        return None

    latest_user_text = _extract_latest_user_text(messages)
    plan: Dict[str, Any] = {
        "version": 1,
        "status": "planned",
        "goal": _shorten(latest_user_text, 160),
        "summary": "系统将基于当前对话动态拆解步骤、执行工具并持续记录轨迹。",
        "max_steps": MAX_TOOL_CALL_STEPS,
        "steps": [],
        "constraints": [],
        "recovery": {
            "resume_supported": True,
            "resume_from_step_id": None,
            "completed_step_ids": [],
            "pending_step_ids": [],
            "failed_step_id": None,
            "suggested_action": "等待模型选择工具后开始执行。",
        },
    }
    for index, rpa in enumerate(required_rpas[:MAX_TOOL_CALL_STEPS], start=1):
        plan["steps"].append(_build_step_from_rpa(rpa, index))
    _refresh_recovery(plan)
    return plan


def _refresh_recovery(plan: Dict[str, Any]) -> None:
    steps = _normalize_steps(plan)
    completed = [step["id"] for step in steps if step.get("status") in {"success", "cached"}]
    pending = [step["id"] for step in steps if step.get("status") in {"planned", "running", "pending"}]
    failed_step = next(
        (step["id"] for step in steps if step.get("status") in {"error", "timeout", "blocked"}),
        None,
    )

    if failed_step:
        status = "needs_attention"
        suggested_action = "失败步骤可在补齐参数、修正范围或完成确认后从当前节点继续。"
        resume_from = failed_step
    elif any(step.get("status") == "pending" for step in steps):
        status = "running"
        suggested_action = "存在后台步骤，系统会在任务完成后继续更新轨迹。"
        resume_from = next((step["id"] for step in steps if step.get("status") == "pending"), None)
    elif any(step.get("status") == "running" for step in steps):
        status = "running"
        suggested_action = "当前正在执行工具步骤。"
        resume_from = next((step["id"] for step in steps if step.get("status") == "running"), None)
    elif steps and all(step.get("status") in {"success", "cached"} for step in steps):
        status = "completed"
        suggested_action = "所有步骤已完成，可沉淀为常用任务。"
        resume_from = None
    elif steps:
        status = "planned"
        suggested_action = "等待下一步工具执行。"
        resume_from = next((step["id"] for step in steps if step.get("status") == "planned"), None)
    else:
        status = "draft"
        suggested_action = "等待模型判断是否需要工具。"
        resume_from = None

    plan["status"] = status
    plan["recovery"] = {
        "resume_supported": True,
        "resume_from_step_id": resume_from,
        "completed_step_ids": completed,
        "pending_step_ids": pending,
        "failed_step_id": failed_step,
        "suggested_action": suggested_action,
    }


def _find_step(plan: Dict[str, Any], tool_name: str) -> Optional[Dict[str, Any]]:
    for step in _normalize_steps(plan):
        if step.get("tool_name") == tool_name and step.get("status") in {"planned", "running", "pending"}:
            return step
    return None


def _append_dynamic_step(plan: Dict[str, Any], tool_name: str, rpa: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    steps = _normalize_steps(plan)
    index = len(steps) + 1
    if isinstance(rpa, dict):
        step = _build_step_from_rpa(rpa, index)
    else:
        step = {
            "id": f"step-{index}",
            "tool_name": tool_name,
            "title": tool_name,
            "status": "planned",
            "required_params": [],
            "outputs": [],
            "supports_batch": False,
            "has_side_effects": False,
            "requires_confirmation": False,
            "can_resume": True,
            "attempts": 0,
            "max_retries": 0,
            "task_id": None,
            "arguments": {},
            "result_preview": "",
            "failure_category": "",
        }
    steps.append(step)
    return step


def _classify_tool_status(status: str, result_preview: str = "") -> str:
    normalized = str(status or "").strip().lower()
    preview = str(result_preview or "").lower()
    if normalized in {"error", "timeout", "blocked"}:
        if "confirm" in preview or "确认" in preview:
            return "confirmation_required"
        if "参数" in preview or "missing" in preview:
            return "missing_parameter"
        if "范围" in preview or "scope" in preview or "路径" in preview:
            return "scope_violation"
        return "tool_error"
    if normalized == "pending":
        return "background_pending"
    return ""


def register_tool_start(
    plan: Optional[Dict[str, Any]],
    tool_name: str,
    tool_args: Dict[str, Any],
    rpa: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    next_plan = _clone(plan)
    step = _find_step(next_plan, tool_name) or _append_dynamic_step(next_plan, tool_name, rpa)
    step["status"] = "running"
    step["arguments"] = tool_args or {}
    step["attempts"] = int(step.get("attempts") or 0) + 1
    _refresh_recovery(next_plan)
    return next_plan


def register_tool_result(
    plan: Optional[Dict[str, Any]],
    tool_name: str,
    tool_status: str,
    result_preview: str,
    task_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    next_plan = _clone(plan)
    step = _find_step(next_plan, tool_name) or _append_dynamic_step(next_plan, tool_name, None)
    step["status"] = str(tool_status or "success")
    step["task_id"] = task_id
    step["result_preview"] = _shorten(result_preview, 240)
    step["failure_category"] = _classify_tool_status(tool_status, result_preview)
    if task_id:
        step["can_resume"] = True
    _refresh_recovery(next_plan)
    return next_plan


def register_task_update(
    plan: Optional[Dict[str, Any]],
    task_id: str,
    task_status: str,
    result_preview: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    next_plan = _clone(plan)
    for step in _normalize_steps(next_plan):
        if step.get("task_id") != task_id:
            continue
        step["status"] = str(task_status or step.get("status") or "pending")
        step["result_preview"] = _shorten(result_preview, 240)
        step["failure_category"] = _classify_tool_status(task_status, result_preview)
        break
    _refresh_recovery(next_plan)
    return next_plan


def add_constraints(
    plan: Optional[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    if not constraints:
        return plan

    next_plan = _clone(plan)
    existing = _normalize_constraints(next_plan)
    seen = {
        (item.get("type"), item.get("tool_name"), item.get("code"), item.get("message"))
        for item in existing
        if isinstance(item, dict)
    }
    for item in constraints:
        if not isinstance(item, dict):
            continue
        key = (item.get("type"), item.get("tool_name"), item.get("code"), item.get("message"))
        if key in seen:
            continue
        existing.append(item)
        seen.add(key)

    blocked = next((item for item in existing if item.get("status") == "blocked"), None)
    if blocked:
        next_plan["status"] = "needs_attention"
        recovery = dict(next_plan.get("recovery") or {})
        recovery["failed_step_id"] = recovery.get("resume_from_step_id")
        recovery["suggested_action"] = blocked.get("message") or "当前步骤被约束层阻断。"
        next_plan["recovery"] = recovery
    else:
        _refresh_recovery(next_plan)
    return next_plan


def finalize_runtime_plan(
    plan: Optional[Dict[str, Any]],
    final_content: str = "",
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    next_plan = _clone(plan)
    if final_content:
        next_plan["final_response_preview"] = _shorten(final_content, 240)
    _refresh_recovery(next_plan)
    return next_plan


def build_reusable_task_suggestion(
    plan: Optional[Dict[str, Any]],
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    steps = [step for step in _normalize_steps(plan) if step.get("tool_name")]
    if not steps or plan.get("status") != "completed":
        return None

    latest_user_text = _shorten(_extract_latest_user_text(messages), 180)
    if not latest_user_text:
        return None

    step_names = [step.get("tool_name") for step in steps if step.get("tool_name")]
    title = latest_user_text.split("，", 1)[0].split(",", 1)[0].strip() or "常用任务"
    return {
        "title": _shorten(title, 36),
        "prompt": latest_user_text,
        "summary": f"复用步骤：{' → '.join(step_names)}",
    }
