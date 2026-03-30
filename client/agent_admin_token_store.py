import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.paths import DATA_DIR


DEFAULT_AGENT_ADMIN_TOKEN_STORE_PATH = DATA_DIR / "agent_admin_tokens.json"
DEFAULT_AGENT_ADMIN_TOKEN_STORE_SCHEMA_VERSION = 1
DEFAULT_TOKEN_ROTATION_WARNING_DAYS = 90


def get_agent_admin_token_store_path() -> Path:
    configured_path = str(os.getenv("AGENT_ADMIN_TOKEN_STORE_PATH", "") or "").strip()
    if configured_path:
        return Path(configured_path)
    return DEFAULT_AGENT_ADMIN_TOKEN_STORE_PATH


def _parse_iso_datetime(raw_value) -> datetime | None:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None

    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_rotation_stale(rotated_at: str) -> bool:
    parsed = _parse_iso_datetime(rotated_at)
    if parsed is None:
        return False
    return datetime.now(timezone.utc) - parsed > timedelta(days=DEFAULT_TOKEN_ROTATION_WARNING_DAYS)


def _build_permissions_warning(path: Path) -> list[str]:
    if os.name != "posix" or not path.exists():
        return []

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return [f"Agent Admin token store 权限过宽（当前 {oct(mode)}），建议使用 chmod 600。"]
    return []


def _normalize_machine_token_entry(machine_id: str, raw_entry, warnings: list[str]) -> tuple[str, dict] | None:
    normalized_machine_id = str(machine_id or "").strip()
    if not normalized_machine_id:
        return None

    if isinstance(raw_entry, dict):
        token = str(raw_entry.get("token") or "").strip()
        rotated_at = str(raw_entry.get("rotated_at") or "").strip()
        note = str(raw_entry.get("note") or "").strip()
    else:
        token = str(raw_entry or "").strip()
        rotated_at = ""
        note = ""

    if not token:
        return None

    if rotated_at and _parse_iso_datetime(rotated_at) is None:
        warnings.append(f"{normalized_machine_id} 的 rotated_at 不是合法 ISO 时间。")
    elif not rotated_at:
        warnings.append(f"{normalized_machine_id} 缺少 rotated_at，建议补充最近轮转时间。")
    elif _is_rotation_stale(rotated_at):
        warnings.append(f"{normalized_machine_id} 的 token 已超过 {DEFAULT_TOKEN_ROTATION_WARNING_DAYS} 天未轮转。")

    return normalized_machine_id, {
        "token": token,
        "rotated_at": rotated_at,
        "note": note,
        "stale": _is_rotation_stale(rotated_at),
    }


def load_agent_admin_token_store() -> dict:
    path = get_agent_admin_token_store_path()
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "schema_version": DEFAULT_AGENT_ADMIN_TOKEN_STORE_SCHEMA_VERSION,
            "updated_at": "",
            "default_token": "",
            "default_rotated_at": "",
            "machine_tokens": {},
            "machine_token_meta": {},
            "warnings": [],
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON，请检查 Agent Admin token store 配置。") from exc
    except OSError as exc:
        raise ValueError(f"无法读取 Agent Admin token store：{path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Agent Admin token store 必须是 JSON 对象。")

    warnings = _build_permissions_warning(path)
    schema_version = int(payload.get("schema_version") or DEFAULT_AGENT_ADMIN_TOKEN_STORE_SCHEMA_VERSION)
    updated_at = str(payload.get("updated_at") or "").strip()
    default_token = str(payload.get("default_token") or "").strip()
    default_rotated_at = str(payload.get("default_rotated_at") or "").strip()
    if default_rotated_at and _parse_iso_datetime(default_rotated_at) is None:
        warnings.append("default_rotated_at 不是合法 ISO 时间。")
    elif default_token and not default_rotated_at:
        warnings.append("default_token 缺少 default_rotated_at，建议补充最近轮转时间。")
    elif default_rotated_at and _is_rotation_stale(default_rotated_at):
        warnings.append(f"default_token 已超过 {DEFAULT_TOKEN_ROTATION_WARNING_DAYS} 天未轮转。")

    raw_machine_tokens = payload.get("machine_tokens") or {}
    if not isinstance(raw_machine_tokens, dict):
        raise ValueError("Agent Admin token store 的 machine_tokens 必须是对象。")

    machine_tokens = {}
    machine_token_meta = {}
    for machine_id, raw_entry in raw_machine_tokens.items():
        normalized = _normalize_machine_token_entry(machine_id, raw_entry, warnings)
        if normalized is None:
            continue
        normalized_machine_id, meta = normalized
        machine_tokens[normalized_machine_id] = meta["token"]
        machine_token_meta[normalized_machine_id] = {
            "rotated_at": meta["rotated_at"],
            "note": meta["note"],
            "stale": meta["stale"],
        }

    return {
        "path": str(path),
        "exists": True,
        "schema_version": schema_version,
        "updated_at": updated_at,
        "default_token": default_token,
        "default_rotated_at": default_rotated_at,
        "machine_tokens": machine_tokens,
        "machine_token_meta": machine_token_meta,
        "warnings": warnings,
    }


def get_agent_admin_token_store_health() -> dict:
    config = load_agent_admin_token_store()
    machine_tokens = []
    for machine_id in sorted(config.get("machine_tokens") or {}):
        meta = dict((config.get("machine_token_meta") or {}).get(machine_id) or {})
        machine_tokens.append(
            {
                "machine_id": machine_id,
                "rotated_at": meta.get("rotated_at") or "",
                "note": meta.get("note") or "",
                "stale": bool(meta.get("stale")),
            }
        )

    return {
        "path": config.get("path"),
        "exists": bool(config.get("exists")),
        "schema_version": config.get("schema_version"),
        "updated_at": config.get("updated_at") or "",
        "warnings": list(config.get("warnings") or []),
        "default_token_configured": bool(config.get("default_token")),
        "default_rotated_at": config.get("default_rotated_at") or "",
        "default_token_stale": _is_rotation_stale(config.get("default_rotated_at") or ""),
        "machine_tokens": machine_tokens,
    }
