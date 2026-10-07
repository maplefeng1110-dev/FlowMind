import hmac
import json
import os
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()

INTERNAL_API_HEADER = "X-FlowMind-Internal-Token"
USER_SESSION_HEADER = "X-FlowMind-Session-Token"
# Workers authenticate HTTP calls to the Registry (AI gateway, artifacts, logs) with the
# same shared secret they already use for the /ws registration.
AGENT_TOKEN_HEADER = "X-FlowMind-Agent-Token"


def get_internal_api_token() -> Optional[str]:
    token = os.getenv("FLOWMIND_INTERNAL_API_TOKEN") or os.getenv("SECRET_KEY")
    return str(token) if token else None


def has_internal_api_token() -> bool:
    return bool(get_internal_api_token())


def build_internal_api_headers(session_token: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = get_internal_api_token()
    if token:
        headers[INTERNAL_API_HEADER] = token
    if session_token:
        headers[USER_SESSION_HEADER] = str(session_token)
    return headers


def validate_internal_api_token(provided_token: Optional[str]) -> bool:
    expected_token = get_internal_api_token()
    if not expected_token or not provided_token:
        return False
    return hmac.compare_digest(str(provided_token), expected_token)


def get_agent_token() -> Optional[str]:
    token = str(os.getenv("FLOWMIND_AGENT_WS_TOKEN", "") or "").strip()
    return token or None


def build_agent_auth_headers(token: Optional[str] = None) -> Dict[str, str]:
    resolved = str(token or "").strip() or get_agent_token()
    return {AGENT_TOKEN_HEADER: resolved} if resolved else {}


def get_agent_token_map() -> Dict[str, str]:
    """Per-machine agent tokens from FLOWMIND_AGENT_TOKENS_JSON ({"machine_id": "token"}).
    A malformed value raises instead of silently falling back to the shared token."""
    raw = str(os.getenv("FLOWMIND_AGENT_TOKENS_JSON", "") or "").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("FLOWMIND_AGENT_TOKENS_JSON must be a JSON object of machine_id -> token")
    return {
        str(machine_id).strip(): str(token).strip()
        for machine_id, token in payload.items()
        if str(machine_id).strip() and str(token or "").strip()
    }


def _token_matches(provided: str, expected: Optional[str]) -> bool:
    return bool(provided) and bool(expected) and hmac.compare_digest(provided.encode(), str(expected).encode())


def validate_agent_token(provided_token: Optional[str]) -> bool:
    """Any configured agent credential: the shared token or one machine's own token."""
    normalized = str(provided_token or "").strip()
    if _token_matches(normalized, get_agent_token()):
        return True
    return any(_token_matches(normalized, token) for token in get_agent_token_map().values())


def validate_agent_registration(machine_id: Optional[str], provided_token: Optional[str]) -> bool:
    """A machine listed in FLOWMIND_AGENT_TOKENS_JSON must register with its own token;
    any other machine may use the shared FLOWMIND_AGENT_WS_TOKEN (if one is set)."""
    normalized = str(provided_token or "").strip()
    machine_token = get_agent_token_map().get(str(machine_id or "").strip())
    if machine_token:
        return _token_matches(normalized, machine_token)
    return _token_matches(normalized, get_agent_token())
