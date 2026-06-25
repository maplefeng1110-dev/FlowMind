"""Client for the Orchestrator AI routing gateway (locate / pick / extract).

The Robot Worker holds the browser and screenshots; per the PRD it POSTs them to
the Orchestrator's AI gateway, which routes to a local-first model and returns
coordinates (locate), a chosen DOM element (pick), or structured JSON (extract).
Phase 1 ships this client plus :class:`FakeAIGateway` for tests; the real
endpoints land on the Registry in Phase 3.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional


class AIGatewayClient:
    def __init__(self, base_url: str, *, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def locate(self, image_png: bytes, intent: str) -> Optional[Dict[str, Any]]:
        import httpx

        payload = {"image_b64": base64.b64encode(image_png).decode(), "intent": intent}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/ai/locate", json=payload)
            response.raise_for_status()
            return response.json()

    async def pick(
        self, elements: List[Dict[str, Any]], intent: str
    ) -> Optional[Dict[str, Any]]:
        """Tier-2 self-healing: ask the gateway to choose the best interactive
        element (by text/role) for ``intent`` from the content-script element list."""
        import httpx

        payload = {"elements": elements, "intent": intent}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/ai/pick", json=payload)
            response.raise_for_status()
            return response.json()

    async def extract(
        self,
        *,
        text: Optional[str] = None,
        image_png: Optional[bytes] = None,
        schema_ref: Optional[str] = None,
        fields: Optional[Dict[str, str]] = None,
    ) -> Any:
        import httpx

        payload: Dict[str, Any] = {"text": text, "schema_ref": schema_ref, "fields": fields}
        if image_png is not None:
            payload["image_b64"] = base64.b64encode(image_png).decode()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/ai/extract", json=payload)
            response.raise_for_status()
            return response.json()


class FakeAIGateway:
    """Deterministic gateway for tests.

    Maps intent substrings to coordinates (``locate_map``) and to a chosen DOM
    selector (``pick_map``). ``pick_map`` lets tests exercise the tier-2 DOM-pick
    self-healing path independently of the visual tier; leave it empty to force
    a fall-through to ``locate``.
    """

    def __init__(
        self,
        locate_map: Optional[Dict[str, tuple]] = None,
        extract_value: Any = None,
        pick_map: Optional[Dict[str, str]] = None,
    ):
        self.locate_map = locate_map or {}
        self.extract_value = extract_value if extract_value is not None else {}
        self.pick_map = pick_map or {}
        self.calls: list = []

    async def locate(self, image_png: bytes, intent: str) -> Dict[str, Any]:
        self.calls.append(("locate", intent))
        for key, (x, y) in self.locate_map.items():
            if key in intent:
                return {"x": x, "y": y, "confidence": 0.99}
        return {"x": 10, "y": 10, "confidence": 0.4}

    async def pick(self, elements: List[Dict[str, Any]], intent: str) -> Dict[str, Any]:
        self.calls.append(("pick", intent))
        for key, selector in self.pick_map.items():
            if key in intent:
                index = next(
                    (el.get("i") for el in elements if el.get("selector") == selector), -1
                )
                return {"index": index, "selector": selector, "confidence": 0.95}
        return {"index": -1, "confidence": 0.0}

    async def extract(self, *, text=None, image_png=None, schema_ref=None, fields=None) -> Any:
        self.calls.append(("extract", schema_ref or fields))
        return self.extract_value
