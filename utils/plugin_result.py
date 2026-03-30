from typing import Any, Dict, Optional


def success_result(data: Optional[Any] = None, message: Optional[str] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"status": "success"}
    if data is not None:
        result["data"] = data
    if message:
        result["message"] = message
    return result


def error_result(message: str, *, error: Optional[str] = None, data: Optional[Any] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "error",
        "message": message,
        "error": error or message,
    }
    if data is not None:
        result["data"] = data
    return result
