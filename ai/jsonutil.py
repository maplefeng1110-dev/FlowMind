"""Lenient JSON parsing for LLM output (handles code fences and surrounding prose)."""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def loads_lenient(content: Optional[str]) -> Any:
    if not isinstance(content, str):
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    # Fallback: grab the outermost {...} object if the model wrapped it in prose.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except Exception:  # noqa: BLE001
            return None
    return None
