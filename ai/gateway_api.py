"""FastAPI router exposing the Orchestrator AI gateway to Robot Workers.

Mounted on the Registry app (``app.include_router(ai_gateway_router)``). Workers
POST a screenshot to ``/ai/locate`` to get click coordinates (visual self-healing)
and text to ``/ai/extract`` to get a validated JSON object.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import dompick as dompick_mod
from . import extract as extract_mod
from . import vision as vision_mod
from .router import AIRouter

router = APIRouter(prefix="/ai", tags=["ai-gateway"])

_singleton: Optional[AIRouter] = None


def get_router() -> AIRouter:
    """Lazily construct a process-wide router. Overridable in tests via
    ``app.dependency_overrides[get_router]``."""
    global _singleton
    if _singleton is None:
        _singleton = AIRouter()
    return _singleton


class LocateRequest(BaseModel):
    image_b64: str
    intent: str


class ExtractRequest(BaseModel):
    text: Optional[str] = None
    image_b64: Optional[str] = None
    schema_ref: Optional[str] = None
    fields: Optional[Dict[str, str]] = None


class PickRequest(BaseModel):
    elements: List[Dict[str, Any]]
    intent: str


@router.get("/providers")
async def providers_endpoint(ai_router: AIRouter = Depends(get_router)) -> Dict[str, Any]:
    return {"providers": ai_router.available(), "stats": ai_router.stats}


@router.post("/locate")
async def locate_endpoint(
    req: LocateRequest, ai_router: AIRouter = Depends(get_router)
) -> Dict[str, Any]:
    try:
        png = base64.b64decode(req.image_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid image_b64: {exc}") from exc
    return await vision_mod.locate(ai_router, png, req.intent)


@router.post("/extract")
async def extract_endpoint(
    req: ExtractRequest, ai_router: AIRouter = Depends(get_router)
) -> Dict[str, Any]:
    try:
        return await extract_mod.extract(
            ai_router, text=req.text, schema_ref=req.schema_ref, fields=req.fields
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pick")
async def pick_endpoint(
    req: PickRequest, ai_router: AIRouter = Depends(get_router)
) -> Dict[str, Any]:
    """DOM-text self-healing tier: choose the best interactive element for an intent."""
    return await dompick_mod.pick(ai_router, elements=req.elements, intent=req.intent)
