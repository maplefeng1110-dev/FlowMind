"""后台任务总结模块：负责把任务结果整理成适合前端展示的文本。"""

import json
from typing import Any, Dict, Optional

from client.ai_manager import ai_manager
from client.web_server_tooling import logger


def _render_task_payload(task: Dict[str, Any]) -> str:
    """把任务对象渲染成稳定的文本，供模板总结和模型兜底复用。"""

    result = task.get("result")
    try:
        rendered = json.dumps(result if result is not None else task, ensure_ascii=False, indent=2)
    except TypeError:
        rendered = str(result if result is not None else task)

    if len(rendered) > 600:
        return f"{rendered[:600]}..."
    return rendered


def _format_task_summary_fallback(tool_name: str, task: Dict[str, Any]) -> str:
    """当没有可用模型时，使用模板文案总结后台任务结果。"""

    rendered = _render_task_payload(task)
    status = str((task.get("result") or {}).get("status") or task.get("status") or "pending")
    if status == "success":
        return f"后台任务 `{tool_name}` 已完成。\n\n结果摘要：\n{rendered or '暂无返回内容。'}"
    if status == "timeout":
        return f"后台任务 `{tool_name}` 已超时。\n\n详情：\n{rendered or '暂无返回内容。'}"
    return f"后台任务 `{tool_name}` 执行失败。\n\n详情：\n{rendered or '暂无返回内容。'}"


async def _generate_background_task_summary(
    tool_name: str,
    task: Dict[str, Any],
    arguments: Optional[Dict[str, Any]] = None,
) -> str:
    """优先用模型生成更自然的后台任务总结，没有模型时回退到模板。"""

    provider = ai_manager.get_preferred_provider()
    if not provider:
        return _format_task_summary_fallback(tool_name, task)

    payload = {
        "tool_name": tool_name,
        "arguments": arguments or {},
        "task_status": task.get("status"),
        "task_result": task.get("result"),
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    system_prompt = (
        "You summarize completed RPA task results for end users in concise Simplified Chinese.\n"
        "Be accurate and grounded in the provided task payload.\n"
        "Mention whether the task succeeded, timed out, or failed.\n"
        "If successful, extract the most important result details.\n"
        "If failed, explain the likely reason from the payload.\n"
        "Keep the response under 6 lines and do not mention internal JSON keys unless needed."
    )
    messages = [
        {
            "role": "user",
            "content": f"请总结这个后台 RPA 任务结果，工具名是 {tool_name}。\n\n{payload_text}",
        }
    ]

    try:
        summary = await ai_manager.simple_chat(provider, messages, system_prompt)
        cleaned = (summary or "").strip()
        if cleaned:
            return cleaned
    except Exception as exc:
        logger.error("Failed to generate background task summary for %s: %s", tool_name, exc)

    return _format_task_summary_fallback(tool_name, task)
