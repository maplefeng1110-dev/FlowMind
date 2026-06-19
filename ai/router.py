"""Local-first hybrid compute router across OpenAI-compatible providers.

A provider is a dict ``{id, kind: 'local'|'cloud', client, model, vision: bool}``
where ``client`` exposes ``.chat.completions.create(...)`` (an ``AsyncOpenAI``
instance or a test double). High-volume vision/extraction tasks prefer local
models; only when no suitable local provider exists do they overflow to cloud,
which is what keeps recurring API cost near zero (PRD 6.3).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class AIRouter:
    def __init__(self, providers: Optional[List[Dict[str, Any]]] = None):
        self.providers = providers if providers is not None else build_providers_from_env()
        # call counters let tests/acceptance assert "zero cloud cost".
        self.stats: Dict[str, int] = {"local": 0, "cloud": 0}

    def available(self) -> List[str]:
        return [p["id"] for p in self.providers]

    def select(self, task_kind: str, need_vision: bool = False) -> Dict[str, Any]:
        def capable(provider: Dict[str, Any]) -> bool:
            return bool(provider.get("vision")) if need_vision else True

        locals_ = [p for p in self.providers if p["kind"] == "local" and capable(p)]
        clouds = [p for p in self.providers if p["kind"] == "cloud" and capable(p)]
        ordered = locals_ + clouds  # local-first for every task kind
        if not ordered:
            raise RuntimeError(
                f"No AI provider available (task={task_kind}, need_vision={need_vision})"
            )
        return ordered[0]

    async def complete(
        self,
        task_kind: str,
        messages: List[Dict[str, Any]],
        *,
        json_mode: bool = False,
        need_vision: bool = False,
        temperature: float = 0.0,
    ) -> str:
        provider = self.select(task_kind, need_vision)
        kwargs: Dict[str, Any] = {
            "model": provider["model"],
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await provider["client"].chat.completions.create(**kwargs)
        self.stats[provider["kind"]] += 1
        return response.choices[0].message.content


def build_providers_from_env() -> List[Dict[str, Any]]:
    """Construct providers from environment variables (locals first, then cloud)."""
    providers: List[Dict[str, Any]] = []
    try:
        from openai import AsyncOpenAI
    except Exception:  # noqa: BLE001 - openai SDK missing
        return providers

    # --- local (preferred) ---
    if _truthy(os.getenv("OLLAMA_ENABLED")) or os.getenv("OLLAMA_BASE_URL"):
        providers.append(
            {
                "id": "ollama",
                "kind": "local",
                "client": AsyncOpenAI(
                    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                    api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
                ),
                "model": os.getenv("OLLAMA_MODEL", "qwen2.5"),
                "vision": _truthy(os.getenv("OLLAMA_VISION", "true")),
            }
        )
    if os.getenv("VLLM_BASE_URL"):
        providers.append(
            {
                "id": "vllm",
                "kind": "local",
                "client": AsyncOpenAI(
                    base_url=os.getenv("VLLM_BASE_URL"),
                    api_key=os.getenv("VLLM_API_KEY", "vllm"),
                ),
                "model": os.getenv("VLLM_MODEL", "Qwen2-VL-7B-Instruct"),
                "vision": _truthy(os.getenv("VLLM_VISION", "true")),
            }
        )

    # --- cloud (overflow for complex reasoning / when no local is available) ---
    if os.getenv("DEEPSEEK_API_KEY"):
        providers.append(
            {
                "id": "deepseek",
                "kind": "cloud",
                "client": AsyncOpenAI(
                    api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com"
                ),
                "model": "deepseek-chat",
                "vision": False,
            }
        )
    if os.getenv("ARK_API_KEY"):
        providers.append(
            {
                "id": "volcengine",
                "kind": "cloud",
                "client": AsyncOpenAI(
                    api_key=os.getenv("ARK_API_KEY"),
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                ),
                "model": os.getenv("ARK_MODEL", "doubao-pro-32k"),
                "vision": _truthy(os.getenv("ARK_VISION", "false")),
            }
        )
    return providers
