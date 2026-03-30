import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        import json

        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _build_json_export(resource: str, items: Any) -> Tuple[bytes, str]:
    import json

    payload = {
        "resource": resource,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items) if isinstance(items, list) else 1,
        "items": items,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8"


def _write_csv(rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> Tuple[bytes, str]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _stringify(row.get(field)) for field in fieldnames})
    return buffer.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"


def _build_tasks_csv(items: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    rows = [
        {
            "id": item.get("id"),
            "status": item.get("status"),
            "rpa_id": item.get("rpa_id"),
            "machine_id": item.get("machine_id"),
            "conversation_id": item.get("conversation_id"),
            "conversation_title": item.get("conversation_title"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "result_status": (item.get("result") or {}).get("status") if isinstance(item.get("result"), dict) else "",
            "result_preview": item.get("result"),
        }
        for item in items
    ]
    return _write_csv(
        rows,
        [
            "id",
            "status",
            "rpa_id",
            "machine_id",
            "conversation_id",
            "conversation_title",
            "created_at",
            "updated_at",
            "result_status",
            "result_preview",
        ],
    )


def _build_common_tasks_csv(items: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    rows = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "prompt": item.get("prompt"),
            "last_used_at": item.get("last_used_at"),
            "updated_at": item.get("updated_at"),
            "created_at": item.get("created_at"),
            "plan": item.get("plan"),
        }
        for item in items
    ]
    return _write_csv(rows, ["id", "title", "summary", "prompt", "last_used_at", "updated_at", "created_at", "plan"])


def _build_schedules_csv(items: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    rows = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "provider": item.get("provider"),
            "schedule_type": item.get("schedule_type"),
            "schedule_config": item.get("schedule_config"),
            "timezone": item.get("timezone"),
            "is_active": item.get("is_active"),
            "next_run_at": item.get("next_run_at"),
            "last_run_at": item.get("last_run_at"),
            "last_status": item.get("last_status"),
            "last_error": item.get("last_error"),
            "common_task_id": item.get("common_task_id"),
            "summary": item.get("summary"),
        }
        for item in items
    ]
    return _write_csv(
        rows,
        [
            "id",
            "title",
            "provider",
            "schedule_type",
            "schedule_config",
            "timezone",
            "is_active",
            "next_run_at",
            "last_run_at",
            "last_status",
            "last_error",
            "common_task_id",
            "summary",
        ],
    )


def _build_schedule_runs_csv(items: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    rows = [
        {
            "id": item.get("id"),
            "schedule_id": item.get("schedule_id"),
            "status": item.get("status"),
            "trigger_source": item.get("trigger_source"),
            "planned_for": item.get("planned_for"),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "provider": item.get("provider"),
            "conversation_id": item.get("conversation_id"),
            "conversation_title": item.get("conversation_title"),
            "response_text": item.get("response_text"),
            "error_text": item.get("error_text"),
        }
        for item in items
    ]
    return _write_csv(
        rows,
        [
            "id",
            "schedule_id",
            "status",
            "trigger_source",
            "planned_for",
            "started_at",
            "finished_at",
            "provider",
            "conversation_id",
            "conversation_title",
            "response_text",
            "error_text",
        ],
    )


def _build_conversation_markdown(conversation_id: str, conversation_title: str, messages: List[Dict[str, Any]]) -> Tuple[bytes, str]:
    lines = [
        f"# 对话导出：{conversation_title or conversation_id}",
        "",
        f"- 导出时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 对话 ID：{conversation_id}",
        "",
    ]
    for message in messages:
        role = "用户" if message.get("role") == "user" else "AI 助手"
        timestamp = str(message.get("timestamp") or "")
        lines.append(f"## {role} {timestamp}".rstrip())
        lines.append("")
        lines.append(str(message.get("content") or ""))
        meta = message.get("meta")
        if isinstance(meta, dict) and meta.get("tool_trace"):
            lines.append("")
            lines.append("```json")
            lines.append(_stringify(meta.get("tool_trace")))
            lines.append("```")
        lines.append("")
    return "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8"


def build_export_bundle(
    resource: str,
    fmt: str,
    *,
    items: Any,
    conversation_id: str | None = None,
    conversation_title: str | None = None,
) -> Tuple[bytes, str, str]:
    normalized_resource = str(resource or "").strip().lower()
    normalized_format = str(fmt or "").strip().lower()
    filename = f"flowmind-{normalized_resource}-{_timestamp()}"

    if normalized_format == "json":
        content, media_type = _build_json_export(normalized_resource, items)
        return content, media_type, f"{filename}.json"

    if normalized_resource == "conversation" and normalized_format == "markdown":
        content, media_type = _build_conversation_markdown(
            str(conversation_id or ""),
            str(conversation_title or ""),
            items if isinstance(items, list) else [],
        )
        return content, media_type, f"{filename}.md"

    if normalized_format != "csv":
        raise ValueError("unsupported export format")

    rows = items if isinstance(items, list) else []
    if normalized_resource == "tasks":
        content, media_type = _build_tasks_csv(rows)
    elif normalized_resource == "common_tasks":
        content, media_type = _build_common_tasks_csv(rows)
    elif normalized_resource == "schedules":
        content, media_type = _build_schedules_csv(rows)
    elif normalized_resource == "schedule_runs":
        content, media_type = _build_schedule_runs_csv(rows)
    else:
        raise ValueError("unsupported csv export resource")

    return content, media_type, f"{filename}.csv"
