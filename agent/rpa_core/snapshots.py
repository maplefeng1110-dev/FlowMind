"""Filesystem sink for failure snapshots (screenshot + HTML + step context).

When a step errors, the interpreter calls ``save(...)`` so the scene is preserved
for debugging (PRD 3.2 exception capture). Refs are returned in the step log and
travel back to the Orchestrator with the task result.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict


class LocalSnapshotSink:
    def __init__(self, base_dir: str, task_id: str = ""):
        self.base_dir = base_dir
        self.task_id = task_id or time.strftime("run-%Y%m%d-%H%M%S")

    async def save(self, *, index: int, step: str, error: str, png: bytes, html: str) -> Dict[str, Any]:
        target = os.path.join(self.base_dir, self.task_id)
        os.makedirs(target, exist_ok=True)
        png_path = os.path.join(target, f"step{index}.png")
        html_path = os.path.join(target, f"step{index}.html")
        with open(png_path, "wb") as handle:
            handle.write(png or b"")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(html or "")
        meta = {"index": index, "step": step, "error": error, "screenshot": png_path, "html": html_path}
        with open(os.path.join(target, f"step{index}.json"), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False)
        return {"screenshot": png_path, "html": html_path, "step": step, "error": error}


class RemoteSnapshotSink:
    """Uploads snapshots to the Orchestrator (``/artifacts``) so backend audit can
    see them even when the worker is a remote machine. Falls back to local files on
    any upload failure."""

    def __init__(self, base_url: str, task_id: str = "", fallback_dir: str = ""):
        self.base_url = base_url.rstrip("/")
        self.task_id = task_id or time.strftime("run-%Y%m%d-%H%M%S")
        self.fallback = LocalSnapshotSink(fallback_dir, self.task_id) if fallback_dir else None

    async def save(self, *, index: int, step: str, error: str, png: bytes, html: str) -> Dict[str, Any]:
        import base64
        import httpx

        refs: Dict[str, Any] = {"step": step, "error": error}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                for kind, content in (("png", png or b""), ("html", (html or "").encode("utf-8"))):
                    response = await client.post(
                        f"{self.base_url}/artifacts",
                        json={
                            "run_id": self.task_id,
                            "index": index,
                            "kind": kind,
                            "content_b64": base64.b64encode(content).decode(),
                        },
                    )
                    response.raise_for_status()
                    refs["screenshot" if kind == "png" else "html"] = response.json().get("url")
            return refs
        except Exception as exc:  # noqa: BLE001 - fall back to local on any upload error
            if self.fallback is not None:
                return await self.fallback.save(index=index, step=step, error=error, png=png, html=html)
            refs["upload_error"] = str(exc)
            return refs
