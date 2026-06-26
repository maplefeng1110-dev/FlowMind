import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import aiosqlite
from dotenv import load_dotenv

from utils.paths import REGISTRY_DB_PATH, ensure_runtime_dirs

load_dotenv()

DB_PATH = str(REGISTRY_DB_PATH)
PASSWORD_HASH_ITERATIONS = max(120000, int(os.getenv("FLOWMIND_PASSWORD_HASH_ITERATIONS", "240000")))
SESSION_TTL_SECONDS = max(3600, int(os.getenv("FLOWMIND_SESSION_TTL_SECONDS", str(7 * 24 * 60 * 60))))
DEFAULT_ADMIN_USERNAME = os.getenv("FLOWMIND_ADMIN_USERNAME", "admin")
BOOTSTRAP_ADMIN_PASSWORD_FILENAME = "bootstrap_admin_password.txt"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
SCHEDULE_TYPES = {"once", "interval", "daily"}
SCHEDULE_RUN_STATUSES = {"queued", "running", "success", "needs_attention", "error"}
DAILY_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DEFAULT_SCHEDULE_TIMEZONE = os.getenv("FLOWMIND_DEFAULT_SCHEDULE_TIMEZONE", "Asia/Shanghai")
SCHEDULE_CLAIM_TTL_SECONDS = max(60, int(os.getenv("FLOWMIND_SCHEDULE_CLAIM_TTL_SECONDS", "900")))


def _utc_now_string() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_to_db_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value: Any) -> datetime:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("datetime value is required")

    variants = [
        normalized.replace("Z", "+00:00"),
        normalized.replace("T", " "),
    ]
    for candidate in variants:
        try:
            parsed = datetime.fromisoformat(candidate)
            break
        except ValueError:
            parsed = None
    if parsed is None:
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"invalid datetime: {normalized}") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_timezone_name(timezone_name: Optional[str]) -> str:
    normalized = str(timezone_name or DEFAULT_SCHEDULE_TIMEZONE).strip() or DEFAULT_SCHEDULE_TIMEZONE
    try:
        ZoneInfo(normalized)
    except Exception as exc:
        raise ValueError(f"invalid timezone: {normalized}") from exc
    return normalized


def _normalize_schedule_type(schedule_type: str) -> str:
    normalized = str(schedule_type or "").strip().lower()
    if normalized not in SCHEDULE_TYPES:
        raise ValueError(f"schedule_type must be one of: {', '.join(sorted(SCHEDULE_TYPES))}")
    return normalized


def _normalize_schedule_run_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in SCHEDULE_RUN_STATUSES:
        raise ValueError(f"run status must be one of: {', '.join(sorted(SCHEDULE_RUN_STATUSES))}")
    return normalized


def _normalize_schedule_config(
    schedule_type: str,
    schedule_config: Optional[Dict[str, Any]],
    timezone_name: str,
    *,
    allow_past_once: bool = False,
) -> Dict[str, Any]:
    normalized_type = _normalize_schedule_type(schedule_type)
    normalized_timezone = _normalize_timezone_name(timezone_name)
    payload = schedule_config if isinstance(schedule_config, dict) else {}

    if normalized_type == "once":
        run_at = payload.get("run_at")
        if not run_at:
            raise ValueError("once schedule requires schedule_config.run_at")
        run_at_dt = _parse_datetime(run_at)
        if not allow_past_once and run_at_dt <= _utc_now():
            raise ValueError("run_at must be in the future")
        return {"run_at": _datetime_to_db_string(run_at_dt)}

    if normalized_type == "interval":
        try:
            interval_minutes = int(payload.get("interval_minutes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("interval schedule requires integer schedule_config.interval_minutes") from exc
        if interval_minutes < 5:
            raise ValueError("interval_minutes must be at least 5")
        if interval_minutes > 60 * 24 * 30:
            raise ValueError("interval_minutes is too large")
        return {"interval_minutes": interval_minutes}

    daily_time = str(payload.get("time") or "").strip()
    if not DAILY_TIME_PATTERN.fullmatch(daily_time):
        raise ValueError("daily schedule requires schedule_config.time in HH:MM format")
    return {"time": daily_time, "timezone": normalized_timezone}


def _compute_next_run_at(
    schedule_type: str,
    schedule_config: Dict[str, Any],
    timezone_name: str,
    *,
    now: Optional[datetime] = None,
    allow_past_once: bool = False,
) -> Optional[str]:
    reference_now = (now or _utc_now()).astimezone(timezone.utc)
    normalized_type = _normalize_schedule_type(schedule_type)
    normalized_timezone = _normalize_timezone_name(timezone_name)
    payload = _normalize_schedule_config(
        normalized_type,
        schedule_config,
        normalized_timezone,
        allow_past_once=allow_past_once,
    )

    if normalized_type == "once":
        run_at_dt = _parse_datetime(payload["run_at"])
        if not allow_past_once and run_at_dt <= reference_now:
            raise ValueError("run_at must be in the future")
        return _datetime_to_db_string(run_at_dt)

    if normalized_type == "interval":
        interval_minutes = int(payload["interval_minutes"])
        return _datetime_to_db_string(reference_now + timedelta(minutes=interval_minutes))

    local_now = reference_now.astimezone(ZoneInfo(normalized_timezone))
    hour_text, minute_text = payload["time"].split(":", 1)
    candidate = local_now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return _datetime_to_db_string(candidate.astimezone(timezone.utc))


def _decode_json_text(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _row_to_schedule(row: sqlite3.Row | aiosqlite.Row | Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["is_active"] = bool(item.get("is_active", 1))
    item["schedule_config"] = _decode_json_text(item.get("schedule_config"), {})
    return item


def _row_to_schedule_run(row: sqlite3.Row | aiosqlite.Row | Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["runtime_plan"] = _decode_json_text(item.get("runtime_plan"), None)
    item["tool_trace"] = _decode_json_text(item.get("tool_trace"), [])
    return item


def _normalize_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in {"admin", "business"}:
        raise ValueError("role must be either 'admin' or 'business'")
    return normalized


def _normalize_username(username: str) -> str:
    normalized = str(username or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("username must be 3-32 chars using letters, numbers, dot, underscore or hyphen")
    return normalized


def _sanitize_display_name(display_name: Optional[str], username: str) -> str:
    cleaned = str(display_name or "").strip()
    return cleaned or username


def _validate_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("password must be at least 8 characters")
    return value


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS)
    encoded_salt = base64.b64encode(salt).decode("ascii")
    encoded_digest = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${encoded_salt}${encoded_digest}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = str(password_hash or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text.encode("ascii"))
        expected_digest = base64.b64decode(digest_text.encode("ascii"))
    except Exception:
        return False

    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _bootstrap_admin_password_path() -> Path:
    return Path(DB_PATH).resolve().parent / BOOTSTRAP_ADMIN_PASSWORD_FILENAME


def _read_bootstrap_admin_password(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("password="):
                value = line.split("=", 1)[1].strip()
                return _validate_password(value)
    except Exception:
        return None
    return None


def _write_bootstrap_admin_password(path: Path, username: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# FlowMind bootstrap admin credentials\n"
        "# Delete this file after rotating the admin password in a secure channel.\n"
        f"username={username}\n"
        f"password={password}\n"
        f"created_at={_utc_now_string()}\n"
    )
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _resolve_default_admin_password(username: str) -> str:
    configured_password = os.getenv("FLOWMIND_ADMIN_PASSWORD")
    if configured_password:
        return _validate_password(configured_password)

    bootstrap_path = _bootstrap_admin_password_path()
    existing_password = _read_bootstrap_admin_password(bootstrap_path)
    if existing_password:
        return existing_password

    generated_password = secrets.token_urlsafe(18)
    _write_bootstrap_admin_password(bootstrap_path, username, generated_password)
    print(
        f"[FlowMind] No FLOWMIND_ADMIN_PASSWORD configured. "
        f"Bootstrap admin credentials written to {bootstrap_path}"
    )
    return generated_password


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        return {row[1] async for row in cursor}


def _row_to_user(row: sqlite3.Row | aiosqlite.Row, assigned_rpa_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    user = dict(row)
    user.pop("password_hash", None)
    user["display_name"] = user.get("display_name") or user.get("username")
    user["is_active"] = bool(user.get("is_active", 1))
    user["assigned_rpa_ids"] = sorted(set(assigned_rpa_ids or []))
    return user


async def _get_user_rpa_map(db: aiosqlite.Connection, user_ids: Sequence[str]) -> Dict[str, List[str]]:
    ids = [str(user_id) for user_id in user_ids if user_id]
    if not ids:
        return {}

    placeholders = ",".join("?" for _ in ids)
    query = (
        "SELECT user_id, rpa_id FROM user_plugin_permissions "
        f"WHERE user_id IN ({placeholders}) ORDER BY user_id ASC, rpa_id ASC"
    )
    mapping: Dict[str, List[str]] = {user_id: [] for user_id in ids}
    async with db.execute(query, ids) as cursor:
        async for row in cursor:
            mapping.setdefault(row["user_id"], []).append(row["rpa_id"])
    return mapping


async def _get_user_row_by_id(db: aiosqlite.Connection, user_id: str) -> Optional[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT id, username, password_hash, role, display_name, is_active, created_at, updated_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ) as cursor:
        return await cursor.fetchone()


async def _ensure_default_admin(db: aiosqlite.Connection) -> None:
    async with db.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
    ) as cursor:
        row = await cursor.fetchone()
        active_admin_count = int(row[0]) if row and row[0] is not None else 0

    if active_admin_count > 0:
        return

    now = _utc_now_string()
    normalized_username = _normalize_username(DEFAULT_ADMIN_USERNAME)
    resolved_password = _resolve_default_admin_password(normalized_username)
    await db.execute(
        """
        INSERT INTO users (id, username, password_hash, role, display_name, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 'admin', ?, 1, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            role = 'admin',
            is_active = 1,
            display_name = excluded.display_name,
            updated_at = excluded.updated_at
        """,
        (
            str(uuid.uuid4()),
            normalized_username,
            _hash_password(resolved_password),
            "系统管理员",
            now,
            now,
        ),
    )


async def init_db():
    ensure_runtime_dirs()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS machines (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                system_info TEXT,
                rpas TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS rpas (
                id TEXT PRIMARY KEY,
                manifest TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                machine_id TEXT NOT NULL,
                rpa_id TEXT NOT NULL,
                params TEXT,
                status TEXT NOT NULL,
                result TEXT,
                conversation_id TEXT,
                conversation_title TEXT,
                owner_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                owner_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                meta TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plugin_permissions (
                user_id TEXT NOT NULL,
                rpa_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, rpa_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS common_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                summary TEXT,
                plan TEXT,
                owner_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_schedules (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                summary TEXT,
                common_task_id TEXT,
                provider TEXT,
                schedule_type TEXT NOT NULL,
                schedule_config TEXT NOT NULL,
                timezone TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT,
                last_run_at TEXT,
                last_status TEXT,
                last_error TEXT,
                claim_token TEXT,
                claim_expires_at TEXT,
                owner_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS task_schedule_runs (
                id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger_source TEXT NOT NULL DEFAULT 'scheduler',
                planned_for TEXT,
                started_at TEXT,
                finished_at TEXT,
                provider TEXT,
                conversation_id TEXT,
                conversation_title TEXT,
                response_text TEXT,
                runtime_plan TEXT,
                tool_trace TEXT,
                error_text TEXT,
                owner_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (schedule_id) REFERENCES task_schedules(id) ON DELETE CASCADE
            )
            """
        )

        message_columns = await _table_columns(db, "messages")
        if "meta" not in message_columns:
            await db.execute("ALTER TABLE messages ADD COLUMN meta TEXT")

        task_columns = await _table_columns(db, "tasks")
        if "conversation_id" not in task_columns:
            await db.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT")
        if "conversation_title" not in task_columns:
            await db.execute("ALTER TABLE tasks ADD COLUMN conversation_title TEXT")
        if "owner_user_id" not in task_columns:
            await db.execute("ALTER TABLE tasks ADD COLUMN owner_user_id TEXT")

        conversation_columns = await _table_columns(db, "conversations")
        if "owner_user_id" not in conversation_columns:
            await db.execute("ALTER TABLE conversations ADD COLUMN owner_user_id TEXT")

        user_columns = await _table_columns(db, "users")
        if "display_name" not in user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        if "is_active" not in user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_owner_created ON conversations(owner_user_id, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_owner_updated ON tasks(owner_user_id, updated_at DESC, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, expires_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_common_tasks_owner_updated ON common_tasks(owner_user_id, updated_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_schedules_owner_updated ON task_schedules(owner_user_id, updated_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_schedules_due ON task_schedules(is_active, next_run_at ASC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_schedule_runs_owner_created ON task_schedule_runs(owner_user_id, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_schedule_runs_schedule_created ON task_schedule_runs(schedule_id, created_at DESC)"
        )
        await _ensure_default_admin(db)
        await db.commit()


async def upsert_rpa_manifests(manifests: List[dict]):
    valid_manifests = [manifest for manifest in manifests if isinstance(manifest, dict) and manifest.get("id")]
    if not valid_manifests:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        for manifest in valid_manifests:
            await db.execute(
                """
                INSERT INTO rpas (id, manifest, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    manifest = excluded.manifest,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (manifest["id"], json.dumps(manifest, ensure_ascii=False)),
            )
        await db.commit()


async def register_machine(machine_id: str, system_info: dict, rpas: list, manifests: List[dict] = None):
    await upsert_rpa_manifests(manifests or [])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO machines (id, status, system_info, rpas, last_heartbeat)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                system_info = excluded.system_info,
                rpas = excluded.rpas,
                last_heartbeat = CURRENT_TIMESTAMP
            """,
            (machine_id, "online", json.dumps(system_info), json.dumps(rpas)),
        )
        await db.commit()


async def update_machine_status(machine_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE machines SET status = ?, last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?",
            (status, machine_id),
        )
        await db.commit()


async def get_stale_online_machine_ids(timeout_seconds: int) -> List[str]:
    safe_timeout = max(1, int(timeout_seconds or 1))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id
            FROM machines
            WHERE status = 'online'
              AND (strftime('%s', 'now') - strftime('%s', last_heartbeat)) >= ?
            ORDER BY id ASC
            """,
            (safe_timeout,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["id"] for row in rows]


async def get_online_machine_map() -> Dict[str, List[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, rpas FROM machines WHERE status = 'online' ORDER BY id ASC") as cursor:
            machine_map: Dict[str, List[str]] = {}
            async for row in cursor:
                try:
                    rpas = json.loads(row["rpas"])
                except Exception:
                    rpas = []
                if isinstance(rpas, list):
                    machine_map[row["id"]] = [str(rpa_id) for rpa_id in rpas if rpa_id]
            return machine_map


async def get_online_machines_for_rpa(rpa_id: str) -> List[str]:
    machine_map = await get_online_machine_map()
    return [machine_id for machine_id, rpas in machine_map.items() if rpa_id in rpas]


async def get_online_rpa_ids() -> set[str]:
    machine_map = await get_online_machine_map()
    return {rpa_id for rpas in machine_map.values() for rpa_id in rpas}


async def get_all_rpas(
    tags: List[str] = None,
    online_only: bool = False,
    include_machine_info: bool = False,
) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT manifest FROM rpas ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()

    machine_map = await get_online_machine_map() if (online_only or include_machine_info) else {}
    online_ids = (
        {rpa_id for rpa_ids in machine_map.values() for rpa_id in rpa_ids}
        if (online_only or include_machine_info)
        else None
    )
    manifests = []
    for row in rows:
        try:
            manifest = json.loads(row["manifest"])
        except Exception:
            continue
        if isinstance(manifest, dict) and manifest.get("id"):
            if online_ids is not None and manifest["id"] not in online_ids:
                continue
            if include_machine_info:
                machine_ids = [machine_id for machine_id, rpa_ids in machine_map.items() if manifest["id"] in rpa_ids]
                manifest = {
                    **manifest,
                    "online_machine_ids": machine_ids,
                    "online_machine_count": len(machine_ids),
                }
            manifests.append(manifest)

    if not tags:
        return manifests
    return [rpa for rpa in manifests if any(tag in rpa.get("tags", []) for tag in tags)]


async def get_available_rpas(tags: List[str] = None) -> List[dict]:
    return await get_all_rpas(tags=tags, online_only=True, include_machine_info=True)


async def get_rpa(rpa_id: str, online_only: bool = False) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT manifest FROM rpas WHERE id = ?", (rpa_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    manifest = json.loads(row[0])
                except Exception:
                    manifest = None
                if isinstance(manifest, dict) and manifest.get("id"):
                    if online_only:
                        online_ids = await get_online_rpa_ids()
                        if manifest["id"] not in online_ids:
                            return None
                    return manifest

    return None


async def get_available_rpa(rpa_id: str) -> Optional[dict]:
    rpa = await get_rpa(rpa_id, online_only=True)
    if not rpa:
        return None
    machine_ids = await get_online_machines_for_rpa(rpa_id)
    return {
        **rpa,
        "online_machine_ids": machine_ids,
        "online_machine_count": len(machine_ids),
    }


async def list_users(include_inactive: bool = False) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT id, username, password_hash, role, display_name, is_active, created_at, updated_at
            FROM users
        """
        params: List[Any] = []
        if not include_inactive:
            query += " WHERE is_active = 1"
        query += " ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, username ASC"
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        user_ids = [row["id"] for row in rows]
        user_rpa_map = await _get_user_rpa_map(db, user_ids)
        return [_row_to_user(row, user_rpa_map.get(row["id"], [])) for row in rows]


async def get_user_by_id(user_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await _get_user_row_by_id(db, user_id)
        if not row:
            return None
        user_rpa_map = await _get_user_rpa_map(db, [user_id])
        return _row_to_user(row, user_rpa_map.get(user_id, []))


async def get_user_by_username(username: str) -> Optional[dict]:
    normalized_username = _normalize_username(username)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, username, password_hash, role, display_name, is_active, created_at, updated_at
            FROM users
            WHERE username = ?
            """,
            (normalized_username,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        user_rpa_map = await _get_user_rpa_map(db, [row["id"]])
        return _row_to_user(row, user_rpa_map.get(row["id"], []))


async def authenticate_user(username: str, password: str) -> Optional[dict]:
    normalized_username = _normalize_username(username)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, username, password_hash, role, display_name, is_active, created_at, updated_at
            FROM users
            WHERE username = ?
            """,
            (normalized_username,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not bool(row["is_active"]):
            return None
        if not _verify_password(str(password or ""), row["password_hash"]):
            return None
        user_rpa_map = await _get_user_rpa_map(db, [row["id"]])
        return _row_to_user(row, user_rpa_map.get(row["id"], []))


async def create_user(
    username: str,
    password: str,
    role: str = "business",
    display_name: Optional[str] = None,
) -> dict:
    normalized_username = _normalize_username(username)
    normalized_role = _normalize_role(role)
    validated_password = _validate_password(password)
    now = _utc_now_string()
    user_id = str(uuid.uuid4())
    payload = (
        user_id,
        normalized_username,
        _hash_password(validated_password),
        normalized_role,
        _sanitize_display_name(display_name, normalized_username),
        1,
        now,
        now,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO users (id, username, password_hash, role, display_name, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"username already exists: {normalized_username}") from exc

    user = await get_user_by_id(user_id)
    if not user:
        raise RuntimeError("failed to create user")
    return user


async def set_user_allowed_rpas(user_id: str, rpa_ids: Sequence[str]) -> dict:
    normalized_rpa_ids = sorted({str(rpa_id).strip() for rpa_id in (rpa_ids or []) if str(rpa_id).strip()})
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        user_row = await _get_user_row_by_id(db, user_id)
        if not user_row:
            raise ValueError("user not found")

        if normalized_rpa_ids:
            placeholders = ",".join("?" for _ in normalized_rpa_ids)
            query = f"SELECT id FROM rpas WHERE id IN ({placeholders})"
            async with db.execute(query, normalized_rpa_ids) as cursor:
                rows = await cursor.fetchall()
            existing_ids = {row["id"] for row in rows}
            missing_ids = [rpa_id for rpa_id in normalized_rpa_ids if rpa_id not in existing_ids]
            if missing_ids:
                raise ValueError(f"unknown rpa ids: {', '.join(missing_ids)}")

        await db.execute("DELETE FROM user_plugin_permissions WHERE user_id = ?", (user_id,))
        if user_row["role"] != "admin":
            for rpa_id in normalized_rpa_ids:
                await db.execute(
                    "INSERT INTO user_plugin_permissions (user_id, rpa_id) VALUES (?, ?)",
                    (user_id, rpa_id),
                )
        await db.commit()

    user = await get_user_by_id(user_id)
    if not user:
        raise RuntimeError("failed to update user permissions")
    return user


async def create_session(user_id: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    session_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(3600, int(ttl_seconds or SESSION_TTL_SECONDS)))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    token_hash = _hash_session_token(session_token)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
        await db.execute(
            """
            INSERT INTO user_sessions (token_hash, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (token_hash, user_id, expires_at),
        )
        await db.commit()
    return session_token


async def get_user_by_session_token(session_token: str) -> Optional[dict]:
    token_hash = _hash_session_token(session_token)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("DELETE FROM user_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
        async with db.execute(
            """
            SELECT users.id, users.username, users.password_hash, users.role, users.display_name, users.is_active, users.created_at, users.updated_at
            FROM user_sessions
            JOIN users ON users.id = user_sessions.user_id
            WHERE user_sessions.token_hash = ?
              AND user_sessions.expires_at > CURRENT_TIMESTAMP
              AND users.is_active = 1
            """,
            (token_hash,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.commit()
            return None
        await db.execute(
            "UPDATE user_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE token_hash = ?",
            (token_hash,),
        )
        user_rpa_map = await _get_user_rpa_map(db, [row["id"]])
        await db.commit()
        return _row_to_user(row, user_rpa_map.get(row["id"], []))


async def delete_session(session_token: str) -> None:
    if not session_token:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_hash_session_token(session_token),))
        await db.commit()


def _build_owner_clause(owner_user_id: Optional[str], include_all: bool, column_name: str) -> tuple[str, tuple[Any, ...]]:
    if include_all or not owner_user_id:
        return "", ()
    return f" AND {column_name} = ?", (owner_user_id,)


async def create_task(
    task_id: str,
    machine_id: str,
    rpa_id: str,
    params: dict,
    status: str = "pending",
    conversation_id: Optional[str] = None,
    conversation_title: Optional[str] = None,
    owner_user_id: Optional[str] = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO tasks (id, machine_id, rpa_id, params, status, conversation_id, conversation_title, owner_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                machine_id,
                rpa_id,
                json.dumps(params),
                status,
                conversation_id,
                conversation_title,
                owner_user_id,
            ),
        )
        await db.commit()


async def update_task_status(task_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, task_id),
        )
        await db.commit()


async def update_task_result(task_id: str, status: str, result: dict = None):
    async with aiosqlite.connect(DB_PATH) as db:
        result_text = json.dumps(result, ensure_ascii=False) if result is not None else None
        await db.execute(
            """
            UPDATE tasks SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result_text, task_id),
        )
        await db.commit()


async def get_task(task_id: str, owner_user_id: Optional[str] = None, include_all: bool = False) -> Optional[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "tasks.owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT tasks.*, machines.status AS machine_status, machines.last_heartbeat AS machine_last_heartbeat
            FROM tasks
            LEFT JOIN machines ON tasks.machine_id = machines.id
            WHERE tasks.id = ?{owner_clause}
            """,
            (task_id, *owner_params),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            task = dict(row)
            for field in ("params", "result"):
                value = task.get(field)
                if not value:
                    continue
                try:
                    task[field] = json.loads(value)
                except Exception:
                    pass
            return task


async def list_tasks(
    limit: int = 50,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
) -> List[dict]:
    safe_limit = max(1, min(int(limit or 50), 200))
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "tasks.owner_user_id")
    filter_clause = ""
    filter_params: list = []
    if status:
        filter_clause += " AND tasks.status = ?"
        filter_params.append(str(status))
    if keyword:
        like = f"%{keyword}%"
        filter_clause += " AND (tasks.rpa_id LIKE ? OR tasks.result LIKE ? OR tasks.conversation_title LIKE ?)"
        filter_params.extend([like, like, like])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT tasks.*, machines.status AS machine_status, machines.last_heartbeat AS machine_last_heartbeat
            FROM tasks
            LEFT JOIN machines ON tasks.machine_id = machines.id
            WHERE 1 = 1{owner_clause}{filter_clause}
            ORDER BY tasks.updated_at DESC, tasks.created_at DESC, tasks.rowid DESC
            LIMIT ?
            """,
            (*owner_params, *filter_params, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()

    tasks = []
    for row in rows:
        task = dict(row)
        for field in ("params", "result"):
            value = task.get(field)
            if not value:
                continue
            try:
                task[field] = json.loads(value)
            except Exception:
                pass
        tasks.append(task)
    return tasks


async def list_machines() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                machines.*,
                COALESCE((
                    SELECT COUNT(*)
                    FROM tasks
                    WHERE tasks.machine_id = machines.id
                      AND tasks.status IN ('pending', 'running')
                ), 0) AS running_task_count
            FROM machines
            ORDER BY
                CASE WHEN machines.status = 'online' THEN 0 ELSE 1 END,
                machines.last_heartbeat DESC,
                machines.id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()

    machines = []
    for row in rows:
        machine = dict(row)
        for field in ("system_info", "rpas"):
            value = machine.get(field)
            if not value:
                machine[field] = {} if field == "system_info" else []
                continue
            try:
                machine[field] = json.loads(value)
            except Exception:
                machine[field] = {} if field == "system_info" else []
        machines.append(machine)
    return machines


async def get_machine(machine_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                machines.*,
                COALESCE((
                    SELECT COUNT(*)
                    FROM tasks
                    WHERE tasks.machine_id = machines.id
                      AND tasks.status IN ('pending', 'running')
                ), 0) AS running_task_count
            FROM machines
            WHERE machines.id = ?
            """,
            (machine_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None

    machine = dict(row)
    for field in ("system_info", "rpas"):
        value = machine.get(field)
        if not value:
            machine[field] = {} if field == "system_info" else []
            continue
        try:
            machine[field] = json.loads(value)
        except Exception:
            machine[field] = {} if field == "system_info" else []
    return machine


async def delete_task_record(
    task_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> bool:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"DELETE FROM tasks WHERE id = ?{owner_clause}",
            (task_id, *owner_params),
        )
        await db.commit()
    return cursor.rowcount > 0


async def delete_machine_record(machine_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
        await db.commit()
    return cursor.rowcount > 0


async def get_conversations(owner_user_id: Optional[str] = None, include_all: bool = False) -> List[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM conversations WHERE 1 = 1{owner_clause} ORDER BY created_at DESC",
            owner_params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_conversation_messages(
    conversation_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> List[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "conversations.owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT messages.*
            FROM messages
            JOIN conversations ON conversations.id = messages.conversation_id
            WHERE messages.conversation_id = ?{owner_clause}
            ORDER BY messages.created_at ASC, messages.rowid ASC
            """,
            (conversation_id, *owner_params),
        ) as cursor:
            rows = await cursor.fetchall()
            messages = []
            for row in rows:
                message = dict(row)
                try:
                    message["meta"] = json.loads(message["meta"]) if message.get("meta") else None
                except Exception:
                    message["meta"] = None
                messages.append(message)
            return messages


async def save_conversation(
    conversation_id: str,
    title: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing_owner_id: Optional[str] = None
        async with db.execute("SELECT owner_user_id FROM conversations WHERE id = ?", (conversation_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                existing_owner_id = row["owner_user_id"]

        if existing_owner_id and owner_user_id and not include_all and existing_owner_id != owner_user_id:
            raise PermissionError("conversation does not belong to current user")

        resolved_owner_id = existing_owner_id or owner_user_id
        await db.execute(
            """
            INSERT INTO conversations (id, title, owner_user_id)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                owner_user_id = COALESCE(conversations.owner_user_id, excluded.owner_user_id)
            """,
            (conversation_id, title, resolved_owner_id),
        )
        await db.commit()


async def save_message(
    message_id: str,
    conversation_id: str,
    role: str,
    content: str,
    timestamp: str,
    meta: Optional[Dict[str, Any]] = None,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if owner_user_id and not include_all:
            async with db.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND owner_user_id = ?",
                (conversation_id, owner_user_id),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                raise PermissionError("conversation does not belong to current user")

        meta_text = json.dumps(meta, ensure_ascii=False) if meta is not None else None
        await db.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, timestamp, meta)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                timestamp = excluded.timestamp,
                meta = excluded.meta
            """,
            (message_id, conversation_id, role, content, timestamp, meta_text),
        )
        await db.commit()


async def delete_conversation(
    conversation_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
):
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            f"DELETE FROM conversations WHERE id = ?{owner_clause}",
            (conversation_id, *owner_params),
        )
        await db.commit()


async def list_common_tasks(
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> List[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT *
            FROM common_tasks
            WHERE 1 = 1{owner_clause}
            ORDER BY COALESCE(last_used_at, updated_at, created_at) DESC, rowid DESC
            """,
            owner_params,
        ) as cursor:
            rows = await cursor.fetchall()

    tasks: List[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["plan"] = json.loads(item["plan"]) if item.get("plan") else None
        except Exception:
            item["plan"] = None
        tasks.append(item)
    return tasks


async def create_common_task(
    title: str,
    prompt: str,
    summary: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
    owner_user_id: Optional[str] = None,
) -> dict:
    normalized_title = str(title or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if not normalized_title:
        raise ValueError("title is required")
    if not normalized_prompt:
        raise ValueError("prompt is required")

    task_id = str(uuid.uuid4())
    plan_text = json.dumps(plan, ensure_ascii=False) if plan is not None else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO common_tasks (id, title, prompt, summary, plan, owner_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (task_id, normalized_title, normalized_prompt, str(summary or "").strip(), plan_text, owner_user_id),
        )
        await db.commit()

    tasks = await list_common_tasks(owner_user_id=owner_user_id, include_all=False)
    created = next((item for item in tasks if item["id"] == task_id), None)
    if not created:
        raise RuntimeError("failed to create common task")
    return created


async def touch_common_task(
    task_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> Optional[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            f"""
            UPDATE common_tasks
            SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?{owner_clause}
            """,
            (task_id, *owner_params),
        )
        await db.commit()
        async with db.execute(
            f"SELECT * FROM common_tasks WHERE id = ?{owner_clause}",
            (task_id, *owner_params),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["plan"] = json.loads(item["plan"]) if item.get("plan") else None
    except Exception:
        item["plan"] = None
    return item


async def delete_common_task(
    task_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> bool:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"DELETE FROM common_tasks WHERE id = ?{owner_clause}",
            (task_id, *owner_params),
        )
        await db.commit()
    return cursor.rowcount > 0


async def get_common_task(
    task_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> Optional[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM common_tasks WHERE id = ?{owner_clause}",
            (task_id, *owner_params),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["plan"] = json.loads(item["plan"]) if item.get("plan") else None
    except Exception:
        item["plan"] = None
    return item


async def list_task_schedules(
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> List[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT *
            FROM task_schedules
            WHERE 1 = 1{owner_clause}
            ORDER BY is_active DESC, COALESCE(next_run_at, updated_at, created_at) ASC, rowid DESC
            """,
            owner_params,
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_schedule(row) for row in rows]


async def get_task_schedule(
    schedule_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> Optional[dict]:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM task_schedules WHERE id = ?{owner_clause}",
            (schedule_id, *owner_params),
        ) as cursor:
            row = await cursor.fetchone()
    return _row_to_schedule(row) if row else None


async def create_task_schedule(
    title: str,
    prompt: str,
    summary: Optional[str] = None,
    common_task_id: Optional[str] = None,
    provider: Optional[str] = None,
    schedule_type: str = "daily",
    schedule_config: Optional[Dict[str, Any]] = None,
    timezone_name: Optional[str] = None,
    is_active: bool = True,
    owner_user_id: Optional[str] = None,
) -> dict:
    normalized_title = str(title or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if not normalized_title:
        raise ValueError("title is required")
    if not normalized_prompt:
        raise ValueError("prompt is required")

    normalized_timezone = _normalize_timezone_name(timezone_name)
    normalized_type = _normalize_schedule_type(schedule_type)
    normalized_config = _normalize_schedule_config(normalized_type, schedule_config, normalized_timezone)
    next_run_at = _compute_next_run_at(
        normalized_type,
        normalized_config,
        normalized_timezone,
        allow_past_once=False,
    )
    schedule_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO task_schedules (
                id, title, prompt, summary, common_task_id, provider, schedule_type, schedule_config,
                timezone, is_active, next_run_at, owner_user_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                schedule_id,
                normalized_title,
                normalized_prompt,
                str(summary or "").strip(),
                str(common_task_id or "").strip() or None,
                str(provider or "").strip() or None,
                normalized_type,
                json.dumps(normalized_config, ensure_ascii=False),
                normalized_timezone,
                1 if is_active else 0,
                next_run_at,
                owner_user_id,
            ),
        )
        await db.commit()

    created = await get_task_schedule(schedule_id, owner_user_id=owner_user_id, include_all=False)
    if not created:
        raise RuntimeError("failed to create task schedule")
    return created


async def update_task_schedule(
    schedule_id: str,
    *,
    title: str,
    prompt: str,
    summary: Optional[str] = None,
    common_task_id: Optional[str] = None,
    provider: Optional[str] = None,
    schedule_type: str,
    schedule_config: Optional[Dict[str, Any]],
    timezone_name: Optional[str],
    is_active: bool,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> Optional[dict]:
    existing = await get_task_schedule(schedule_id, owner_user_id=owner_user_id, include_all=include_all)
    if not existing:
        return None

    normalized_title = str(title or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if not normalized_title:
        raise ValueError("title is required")
    if not normalized_prompt:
        raise ValueError("prompt is required")

    normalized_timezone = _normalize_timezone_name(timezone_name)
    normalized_type = _normalize_schedule_type(schedule_type)
    normalized_config = _normalize_schedule_config(normalized_type, schedule_config, normalized_timezone)
    next_run_at = _compute_next_run_at(
        normalized_type,
        normalized_config,
        normalized_timezone,
        allow_past_once=False,
    )

    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE task_schedules
            SET title = ?, prompt = ?, summary = ?, common_task_id = ?, provider = ?, schedule_type = ?,
                schedule_config = ?, timezone = ?, is_active = ?, next_run_at = ?, claim_token = NULL,
                claim_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?{owner_clause}
            """,
            (
                normalized_title,
                normalized_prompt,
                str(summary or "").strip(),
                str(common_task_id or "").strip() or None,
                str(provider or "").strip() or None,
                normalized_type,
                json.dumps(normalized_config, ensure_ascii=False),
                normalized_timezone,
                1 if is_active else 0,
                next_run_at,
                schedule_id,
                *owner_params,
            ),
        )
        await db.commit()
    return await get_task_schedule(schedule_id, owner_user_id=owner_user_id, include_all=include_all)


async def set_task_schedule_active(
    schedule_id: str,
    is_active: bool,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> Optional[dict]:
    existing = await get_task_schedule(schedule_id, owner_user_id=owner_user_id, include_all=include_all)
    if not existing:
        return None

    next_run_at = existing.get("next_run_at")
    if is_active:
        next_run_at = _compute_next_run_at(
            str(existing.get("schedule_type") or ""),
            existing.get("schedule_config") if isinstance(existing.get("schedule_config"), dict) else {},
            str(existing.get("timezone") or DEFAULT_SCHEDULE_TIMEZONE),
            allow_past_once=False,
        )

    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE task_schedules
            SET is_active = ?, next_run_at = ?, claim_token = NULL, claim_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?{owner_clause}
            """,
            (1 if is_active else 0, next_run_at, schedule_id, *owner_params),
        )
        await db.commit()
    return await get_task_schedule(schedule_id, owner_user_id=owner_user_id, include_all=include_all)


async def delete_task_schedule(
    schedule_id: str,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> bool:
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cursor = await db.execute(
            f"DELETE FROM task_schedules WHERE id = ?{owner_clause}",
            (schedule_id, *owner_params),
        )
        await db.commit()
    return cursor.rowcount > 0


async def list_task_schedule_runs(
    limit: int = 50,
    schedule_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    include_all: bool = False,
) -> List[dict]:
    safe_limit = max(1, min(int(limit or 50), 200))
    owner_clause, owner_params = _build_owner_clause(owner_user_id, include_all, "owner_user_id")
    params: List[Any] = []
    schedule_clause = ""
    if schedule_id:
        schedule_clause = " AND schedule_id = ?"
        params.append(schedule_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT *
            FROM task_schedule_runs
            WHERE 1 = 1{owner_clause}{schedule_clause}
            ORDER BY COALESCE(started_at, created_at) DESC, rowid DESC
            LIMIT ?
            """,
            (*owner_params, *params, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_schedule_run(row) for row in rows]


async def create_internal_session_for_user(user_id: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    user = await get_user_by_id(user_id)
    if not user or not bool(user.get("is_active", True)):
        raise ValueError("user not found or inactive")
    return await create_session(user_id, ttl_seconds=ttl_seconds)


async def claim_due_task_schedules(limit: int = 3) -> List[dict]:
    safe_limit = max(1, min(int(limit or 3), 20))
    now_dt = _utc_now()
    now_text = _datetime_to_db_string(now_dt)
    claim_expires_at = _datetime_to_db_string(now_dt + timedelta(seconds=SCHEDULE_CLAIM_TTL_SECONDS))
    claimed_items: List[dict] = []

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT *
            FROM task_schedules
            WHERE is_active = 1
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
              AND (claim_token IS NULL OR claim_expires_at IS NULL OR claim_expires_at <= ?)
            ORDER BY next_run_at ASC, rowid ASC
            LIMIT ?
            """,
            (now_text, now_text, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            claim_token = str(uuid.uuid4())
            run_id = str(uuid.uuid4())
            cursor = await db.execute(
                """
                UPDATE task_schedules
                SET claim_token = ?, claim_expires_at = ?, last_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND is_active = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                  AND (claim_token IS NULL OR claim_expires_at IS NULL OR claim_expires_at <= ?)
                """,
                (claim_token, claim_expires_at, "queued", row["id"], now_text, now_text),
            )
            if cursor.rowcount <= 0:
                continue

            await db.execute(
                """
                INSERT INTO task_schedule_runs (
                    id, schedule_id, status, trigger_source, planned_for, provider, owner_user_id, created_at, updated_at
                )
                VALUES (?, ?, 'queued', 'scheduler', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    run_id,
                    row["id"],
                    row["next_run_at"],
                    row["provider"],
                    row["owner_user_id"],
                ),
            )
            claimed_items.append(
                {
                    "claim_token": claim_token,
                    "schedule": _row_to_schedule(
                        {
                            **dict(row),
                            "claim_token": claim_token,
                            "claim_expires_at": claim_expires_at,
                            "last_status": "queued",
                        }
                    ),
                    "run": _row_to_schedule_run(
                        {
                            "id": run_id,
                            "schedule_id": row["id"],
                            "status": "queued",
                            "trigger_source": "scheduler",
                            "planned_for": row["next_run_at"],
                            "started_at": None,
                            "finished_at": None,
                            "provider": row["provider"],
                            "conversation_id": None,
                            "conversation_title": None,
                            "response_text": None,
                            "runtime_plan": None,
                            "tool_trace": None,
                            "error_text": None,
                            "owner_user_id": row["owner_user_id"],
                            "created_at": now_text,
                            "updated_at": now_text,
                        }
                    ),
                }
            )
        await db.commit()
    return claimed_items


async def mark_task_schedule_run_started(schedule_id: str, run_id: str, claim_token: str) -> Optional[dict]:
    normalized_claim_token = str(claim_token or "").strip()
    if not normalized_claim_token:
        raise ValueError("claim_token is required")
    started_at = _datetime_to_db_string(_utc_now())

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM task_schedules WHERE id = ? AND claim_token = ?",
            (schedule_id, normalized_claim_token),
        ) as cursor:
            schedule_row = await cursor.fetchone()
        if not schedule_row:
            return None

        cursor = await db.execute(
            """
            UPDATE task_schedule_runs
            SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND schedule_id = ?
            """,
            (started_at, run_id, schedule_id),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None

        await db.execute(
            """
            UPDATE task_schedules
            SET last_status = 'running', last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (schedule_id,),
        )
        await db.commit()

        async with db.execute("SELECT * FROM task_schedule_runs WHERE id = ?", (run_id,)) as cursor:
            run_row = await cursor.fetchone()
    return _row_to_schedule_run(run_row) if run_row else None


async def complete_task_schedule_run(
    schedule_id: str,
    run_id: str,
    claim_token: str,
    *,
    status: str,
    response_text: Optional[str] = None,
    runtime_plan: Optional[Dict[str, Any]] = None,
    tool_trace: Optional[List[Dict[str, Any]]] = None,
    error_text: Optional[str] = None,
    conversation_id: Optional[str] = None,
    conversation_title: Optional[str] = None,
    provider: Optional[str] = None,
) -> Optional[dict]:
    normalized_claim_token = str(claim_token or "").strip()
    if not normalized_claim_token:
        raise ValueError("claim_token is required")
    normalized_status = _normalize_schedule_run_status(status)
    finished_at_dt = _utc_now()
    finished_at = _datetime_to_db_string(finished_at_dt)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM task_schedules WHERE id = ? AND claim_token = ?",
            (schedule_id, normalized_claim_token),
        ) as cursor:
            schedule_row = await cursor.fetchone()
        if not schedule_row:
            return None

        schedule = _row_to_schedule(schedule_row)
        if schedule["schedule_type"] == "once":
            next_run_at = None
            is_active = 0
        else:
            next_run_at = _compute_next_run_at(
                str(schedule.get("schedule_type") or ""),
                schedule.get("schedule_config") if isinstance(schedule.get("schedule_config"), dict) else {},
                str(schedule.get("timezone") or DEFAULT_SCHEDULE_TIMEZONE),
                now=finished_at_dt,
                allow_past_once=False,
            )
            is_active = 1 if schedule.get("is_active") else 0

        cursor = await db.execute(
            """
            UPDATE task_schedule_runs
            SET status = ?, finished_at = ?, provider = COALESCE(?, provider), conversation_id = ?, conversation_title = ?,
                response_text = ?, runtime_plan = ?, tool_trace = ?, error_text = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND schedule_id = ?
            """,
            (
                normalized_status,
                finished_at,
                str(provider or "").strip() or None,
                str(conversation_id or "").strip() or None,
                str(conversation_title or "").strip() or None,
                str(response_text or "").strip() or None,
                json.dumps(runtime_plan, ensure_ascii=False) if runtime_plan is not None else None,
                json.dumps(tool_trace, ensure_ascii=False) if tool_trace is not None else None,
                str(error_text or "").strip() or None,
                run_id,
                schedule_id,
            ),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return None

        await db.execute(
            """
            UPDATE task_schedules
            SET last_run_at = ?, last_status = ?, last_error = ?, next_run_at = ?, is_active = ?,
                claim_token = NULL, claim_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                finished_at,
                normalized_status,
                str(error_text or "").strip() or None,
                next_run_at,
                is_active,
                schedule_id,
            ),
        )
        await db.commit()

        async with db.execute("SELECT * FROM task_schedule_runs WHERE id = ?", (run_id,)) as cursor:
            run_row = await cursor.fetchone()
    return _row_to_schedule_run(run_row) if run_row else None


# ==================== Centralized application logs ====================
_APP_LOGS_DDL = (
    "CREATE TABLE IF NOT EXISTS app_logs ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, "
    "source TEXT, logger TEXT, message TEXT)"
)


async def insert_logs(records) -> int:
    rows = [
        (
            str(r.get("ts") or ""),
            str(r.get("level") or ""),
            str(r.get("source") or ""),
            str(r.get("logger") or ""),
            str(r.get("message") or "")[:4000],
        )
        for r in (records or [])
        if isinstance(r, dict)
    ]
    if not rows:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_APP_LOGS_DDL)
        await db.executemany(
            "INSERT INTO app_logs (ts, level, source, logger, message) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()
    return len(rows)


async def query_logs(limit: int = 200, level=None, source=None, keyword=None):
    safe_limit = max(1, min(int(limit or 200), 1000))
    clause = ""
    params: list = []
    if level:
        clause += " AND level = ?"
        params.append(str(level))
    if source:
        clause += " AND source LIKE ?"
        params.append(f"%{source}%")
    if keyword:
        clause += " AND (message LIKE ? OR logger LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_APP_LOGS_DDL)
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM app_logs WHERE 1 = 1{clause} ORDER BY id DESC LIMIT ?",
            (*params, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]
