"""DOM-text element picker: choose the best interactive element for an intent.

This is the cheap middle tier of self-healing, sitting between deterministic CSS
selectors and VLM coordinate location (``vision.locate``). Given a compact list of
interactive elements serialized by the content script and a natural-language
intent ("点击登录按钮"), a local-first text LLM returns the *index* of the best
match. It avoids screenshots/vision for the common case where an element's
selector changed but its visible text / role still identifies it — faster,
cheaper, and free of coordinate drift. Falls back to ``index = -1`` when unsure so
the caller can escalate to the visual tier.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .jsonutil import loads_lenient

PICK_SYSTEM = (
    "你是一个网页元素选择器。给定页面上可交互元素的列表(JSON)和一个操作意图，"
    '选出最匹配的那一个，只返回JSON：{"index": 整数, "confidence": 0到1之间的数字}。'
    "index 取自元素的 i 字段；若没有合适的元素，index 返回 -1。只输出JSON本身，不要解释。"
)

# Fields worth showing the model; keep the payload compact so small local models stay fast.
_VISIBLE_FIELDS = ("i", "tag", "type", "text", "id", "name", "placeholder", "aria", "role")


def _render_elements(elements: List[Dict[str, Any]], limit: int) -> str:
    compact = []
    for el in elements[:limit]:
        item = {k: el[k] for k in _VISIBLE_FIELDS if el.get(k) not in (None, "")}
        compact.append(item)
    return json.dumps(compact, ensure_ascii=False)


async def pick(
    router: Any,
    *,
    elements: List[Dict[str, Any]],
    intent: str,
    limit: int = 120,
    max_retries: int = 1,
) -> Dict[str, Any]:
    """Return ``{index, confidence, selector?}``. ``index`` is -1 when no element
    matches (or when ``elements`` is empty / the model never returns valid JSON)."""
    if not elements:
        return {"index": -1, "confidence": 0.0}

    messages = [
        {"role": "system", "content": PICK_SYSTEM},
        {
            "role": "user",
            "content": f"操作意图：{intent}\n\n可交互元素：\n{_render_elements(elements, limit)}",
        },
    ]
    for _ in range(max_retries + 1):
        content = await router.complete("dompick", messages, json_mode=True)
        parsed = _parse_pick(loads_lenient(content), elements)
        if parsed is not None:
            return parsed
        messages.append(
            {"role": "user", "content": '请只返回形如 {"index": 数字, "confidence": 数字} 的JSON。'}
        )
    return {"index": -1, "confidence": 0.0}


def _parse_pick(data: Any, elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict) or "index" not in data:
        return None
    try:
        index = int(data["index"])
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    result: Dict[str, Any] = {"index": index, "confidence": confidence}
    if index >= 0:
        for el in elements:
            if el.get("i") == index and el.get("selector"):
                result["selector"] = el["selector"]
                break
    return result
