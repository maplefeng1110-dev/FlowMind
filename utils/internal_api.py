import hmac
import os
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()

INTERNAL_API_HEADER = "X-FlowMind-Internal-Token"
USER_SESSION_HEADER = "X-FlowMind-Session-Token"


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
