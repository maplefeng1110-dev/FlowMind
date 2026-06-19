"""Structured extraction: force JSON Mode to turn free text into a Key-Value dict.

Routes to a local-first provider, enforces JSON output, validates required fields,
and retries on malformed/incomplete output — targeting the PRD's <1% JSON error rate.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .jsonutil import loads_lenient

EXTRACT_SYSTEM = (
    "你是一个严格的数据结构化器。把用户提供的内容解析为一个JSON对象，"
    "只输出JSON本身，不要任何解释或多余文本。"
)


def _describe_schema(schema_ref: Optional[str], fields: Optional[Dict[str, str]]) -> str:
    if fields:
        parts = [f'"{name}"（{desc or name}）' for name, desc in fields.items()]
        return "包含以下字段的对象：" + "、".join(parts)
    if schema_ref:
        return f"符合 schema「{schema_ref}」的对象"
    return "一个键值对象"


async def extract(
    router: Any,
    *,
    text: Optional[str] = None,
    schema_ref: Optional[str] = None,
    fields: Optional[Dict[str, str]] = None,
    max_retries: int = 2,
) -> Dict[str, Any]:
    spec = _describe_schema(schema_ref, fields)
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"请从以下内容中提取{spec}：\n\n{text or ''}"},
    ]

    last_error = "no response"
    for _ in range(max_retries + 1):
        content = await router.complete("extract", messages, json_mode=True)
        data = loads_lenient(content)
        if isinstance(data, dict):
            missing = [key for key in (fields or {}) if key not in data]
            if not missing:
                return data
            last_error = f"missing fields: {missing}"
            messages.append({"role": "assistant", "content": content or ""})
            messages.append(
                {"role": "user", "content": f"缺少字段 {missing}，请补全后只输出完整JSON对象。"}
            )
        else:
            last_error = "output was not valid JSON"
            messages.append({"role": "user", "content": "上一次输出不是合法JSON，请只输出JSON对象。"})

    raise ValueError(f"ai_extract failed to produce valid JSON ({last_error})")
