import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException, Request, Response, status

from registry.db import get_user_by_session_token

SESSION_COOKIE_NAME = os.getenv("FLOWMIND_SESSION_COOKIE", "flowmind_session")


def _resolve_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not str(raw_value).strip():
        return max(minimum, int(default))
    try:
        return max(minimum, int(str(raw_value).strip()))
    except (TypeError, ValueError):
        return max(minimum, int(default))


SESSION_COOKIE_MAX_AGE = _resolve_int_env("FLOWMIND_SESSION_TTL_SECONDS", 7 * 24 * 60 * 60, minimum=3600)


def _resolve_cookie_secure(request: Optional[Request] = None) -> bool:
    configured = os.getenv("FLOWMIND_SESSION_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    if request is None:
        return False
    return request.url.scheme == "https"


@dataclass(slots=True)
class AuthUser:
    id: str
    username: str
    role: str
    display_name: str
    is_active: bool
    assigned_rpa_ids: List[str]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def allowed_rpa_ids(self) -> Optional[Set[str]]:
        if self.is_admin:
            return None
        return set(self.assigned_rpa_ids)


def _normalize_user_payload(user: Optional[Dict[str, Any]]) -> Optional[AuthUser]:
    if not user:
        return None
    assigned_rpa_ids = sorted({str(rpa_id) for rpa_id in user.get("assigned_rpa_ids", []) if rpa_id})
    return AuthUser(
        id=str(user["id"]),
        username=str(user["username"]),
        role=str(user["role"]),
        display_name=str(user.get("display_name") or user["username"]),
        is_active=bool(user.get("is_active", True)),
        assigned_rpa_ids=assigned_rpa_ids,
    )


def serialize_user(user: AuthUser | Dict[str, Any]) -> Dict[str, Any]:
    current = user if isinstance(user, AuthUser) else _normalize_user_payload(user)
    if not current:
        raise ValueError("user is required")
    return {
        "id": current.id,
        "username": current.username,
        "role": current.role,
        "display_name": current.display_name,
        "is_active": current.is_active,
        "assigned_rpa_ids": list(current.assigned_rpa_ids),
        "has_all_rpa_access": current.is_admin,
    }


def apply_login_cookie(response: Response, session_token: str, request: Optional[Request] = None) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_resolve_cookie_secure(request),
        path="/",
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax")


def get_session_token_from_request(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    return request.cookies.get(SESSION_COOKIE_NAME)


async def get_current_user_optional(request: Optional[Request]) -> Optional[AuthUser]:
    session_token = get_session_token_from_request(request)
    if not session_token:
        return None
    return _normalize_user_payload(await get_user_by_session_token(session_token))


async def require_current_user(request: Request) -> AuthUser:
    user = await get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录后再继续操作")
    return user


async def require_admin_user(request: Request) -> AuthUser:
    user = await require_current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员账号可执行此操作")
    return user


def filter_rpas_for_user(rpas: Iterable[Dict[str, Any]], user: Optional[AuthUser]) -> List[Dict[str, Any]]:
    items = [rpa for rpa in rpas if isinstance(rpa, dict) and rpa.get("id")]
    if not user or user.is_admin:
        return items
    allowed_ids = user.allowed_rpa_ids or set()
    return [rpa for rpa in items if rpa["id"] in allowed_ids]


def ensure_rpa_allowed(user: Optional[AuthUser], rpa_id: str) -> None:
    if not user or user.is_admin:
        return
    allowed_ids = user.allowed_rpa_ids or set()
    if rpa_id not in allowed_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"当前账号没有插件 `{rpa_id}` 的使用权限",
        )
