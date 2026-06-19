"""Visual locator: ask a vision model for the click coordinates of a target.

Implements the core of the PRD self-healing lifecycle — given a screenshot and a
natural-language intent ("点击登录按钮"), return ``{x, y, confidence}`` in screenshot
pixel space so the worker can perform an absolute-position click.
"""
from __future__ import annotations

import base64
from typing import Any, Dict

from .jsonutil import loads_lenient

LOCATE_SYSTEM = (
    "你是一个UI视觉定位器。根据用户意图，在给定截图中找到目标元素，"
    '只返回JSON：{"x": 数字, "y": 数字, "confidence": 0到1之间的数字}。'
    "坐标为目标中心点相对截图左上角的像素值。无法定位时 confidence 设为 0。"
)


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def locate(router: Any, image_png: bytes, intent: str) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": LOCATE_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"目标：{intent}"},
                {"type": "image_url", "image_url": {"url": _data_uri(image_png)}},
            ],
        },
    ]
    content = await router.complete("vision", messages, json_mode=True, need_vision=True)
    return _parse_coords(content)


def _parse_coords(content: str) -> Dict[str, Any]:
    data = loads_lenient(content)
    miss = {"x": None, "y": None, "confidence": 0.0}
    if not isinstance(data, dict) or "x" not in data or "y" not in data:
        return miss
    try:
        return {
            "x": float(data["x"]),
            "y": float(data["y"]),
            "confidence": float(data.get("confidence", 0.0)),
        }
    except (TypeError, ValueError):
        return miss
