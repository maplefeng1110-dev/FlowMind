"""Step interpreter for rpa_flow: DSL execution + tiered AI self-healing.

The runner walks a :class:`~flow_dsl.Flow`, dispatching each step against a
:class:`~driver.BrowserDriver`. When a selector-based action fails and the step
allows healing, it escalates through two tiers: ② a DOM-text element pick
(serialize interactive elements -> AI gateway returns the best match's selector,
no screenshot, robust to selector churn) and, failing that, ③ visual location
(screenshot -> VLM coordinates -> absolute click), implementing the PRD
self-healing lifecycle.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from .driver import BrowserDriver, LocatorError
from .flow_dsl import Flow, Step, substitute

# A short first attempt so that, when a selector is stale, the ai_click fallback
# still completes well within the PRD's 3-second self-healing budget.
PROBE_TIMEOUT_MS = 1500


class FlowRunner:
    def __init__(
        self,
        driver: BrowserDriver,
        *,
        ai_gateway: Optional[Any] = None,
        db_writer: Optional[Any] = None,
        env: Optional[Dict[str, Any]] = None,
        snapshot_sink: Optional[Any] = None,
    ):
        self.driver = driver
        self.ai = ai_gateway
        self.db = db_writer
        self.env = env or {}
        self.snapshot_sink = snapshot_sink
        self.vars: Dict[str, Any] = {}
        self.log: List[Dict[str, Any]] = []

    async def run(
        self,
        flow: Flow,
        *,
        start_index: int = 0,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the flow. ``start_index`` + ``variables`` resume a previous run
        from a failed step with its accumulated state (see ``failed_index`` in the
        returned result). Each step retries up to ``step.retries`` times."""
        if variables:
            self.vars.update(variables)

        status = "success"
        error: Optional[str] = None
        failed_index: Optional[int] = None

        for index, step in enumerate(flow.steps):
            if index < start_index:
                continue

            entry: Dict[str, Any] = {
                "index": index,
                "action": step.action,
                "target": step.describe(),
            }
            started = time.monotonic()
            attempts = max(1, step.retries + 1)
            outcome = "failed"

            for attempt in range(attempts):
                try:
                    healed = await self._dispatch(step)
                    entry["status"] = "success"
                    if healed:
                        entry["healed"] = True
                    if attempt:
                        entry["attempts"] = attempt + 1
                    outcome = "success"
                    break
                except Exception as exc:  # noqa: BLE001 - every step failure is reported
                    if attempt < attempts - 1:
                        await asyncio.sleep(max(0, step.retry_delay_ms) / 1000)
                        continue
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    if attempts > 1:
                        entry["attempts"] = attempts
                    snapshot = await self._capture_snapshot(index, step, exc)
                    if snapshot:
                        entry["snapshot"] = snapshot
                    outcome = "skipped" if step.optional else "failed"

            entry["ms"] = round((time.monotonic() - started) * 1000)
            if outcome == "skipped":
                entry["skipped"] = True
            self.log.append(entry)

            if outcome == "failed":
                status, error, failed_index = "error", entry["error"], index
                break

        result: Dict[str, Any] = {"status": status, "vars": self.vars, "steps": self.log}
        if error:
            result["error"] = error
        if failed_index is not None:
            result["failed_index"] = failed_index
        return result

    # -- dispatch -----------------------------------------------------------
    async def _dispatch(self, step: Step) -> bool:
        """Execute one step. Returns True when completed via self-healing fallback."""
        action = step.action
        if action == "goto":
            await self.driver.goto(self._sub(step.url) or "", step.timeout_ms)
            return False
        if action == "click":
            return await self._click(step)
        if action == "type":
            return await self._type(step)
        if action == "ai_click":
            return await self._heal_click(step)
        if action == "scroll":
            await self.driver.scroll(step.direction, step.amount)
            return False
        if action == "extract_text":
            await self._extract_text(step)
            return False
        if action == "ai_extract":
            await self._ai_extract(step)
            return False
        if action == "wait":
            await asyncio.sleep(min(max(step.amount, 0), 10000) / 1000)
            return False
        if action == "save_db":
            await self._save_db(step)
            return False
        raise ValueError(f"Unsupported action: {action}")

    # -- basic actions with self-healing ------------------------------------
    async def _click(self, step: Step) -> bool:
        selector = self._sub(step.selector)
        if not selector:
            return await self._heal_click(step)
        probe = PROBE_TIMEOUT_MS if (step.heal and self.ai) else step.timeout_ms
        try:
            await self.driver.click(selector, probe)
            return False
        except LocatorError:
            if step.heal and self.ai:
                return await self._heal_click(step)
            raise

    async def _type(self, step: Step) -> bool:
        selector = self._sub(step.selector)
        text = self._sub(step.text) or ""
        if selector:
            probe = PROBE_TIMEOUT_MS if (step.heal and self.ai) else step.timeout_ms
            try:
                await self.driver.type(selector, text, probe)
                return False
            except LocatorError:
                if not (step.heal and self.ai):
                    raise
        if not self.ai:
            raise LocatorError("type fallback requires an AI gateway")
        intent = self._sub(step.intent) or selector or "input field"
        # Tier 2: DOM-text pick -> type straight into the chosen field.
        picked = await self._dom_pick(intent)
        if picked:
            try:
                await self.driver.type(picked, text, PROBE_TIMEOUT_MS)
                return True
            except LocatorError:
                pass
        # Tier 3: visual locate -> click -> type into the focused element.
        coords = await self._locate(intent)
        await self.driver.click_xy(coords[0], coords[1])
        await self.driver.type_text(text)
        return True

    # -- AI-native actions & tiered self-healing ----------------------------
    async def _heal_click(self, step: Step) -> bool:
        """Heal an absent/failed click via two tiers, cheap to costly:
        ② serialize interactive elements -> AI gateway picks one -> click its
        selector (no screenshot, robust to selector churn when text/role is
        unchanged); ③ screenshot -> VLM coordinates -> absolute click as the
        last-resort visual fallback."""
        intent = self._sub(step.intent) or self._sub(step.selector) or ""
        selector = await self._dom_pick(intent)
        if selector:
            try:
                await self.driver.click(selector, PROBE_TIMEOUT_MS)
                return True
            except LocatorError:
                pass
        coords = await self._locate(intent)
        await self.driver.click_xy(coords[0], coords[1])
        return True

    async def _dom_pick(self, intent: str) -> Optional[str]:
        """Tier-2 self-healing. Ask the AI gateway to choose the best interactive
        element for ``intent`` from the content-script element list and return its
        selector. Returns None when the gateway lacks ``pick``, the page exposes no
        elements, the call fails, or nothing matched — so the caller escalates to
        the visual tier."""
        pick = getattr(self.ai, "pick", None)
        if not callable(pick):
            return None
        try:
            elements = await self.driver.list_interactive()
        except Exception:  # noqa: BLE001 - DOM listing is best-effort
            return None
        if not elements:
            return None
        try:
            chosen = await pick(elements, intent)
        except Exception:  # noqa: BLE001 - any failure falls through to the visual tier
            return None
        if not isinstance(chosen, dict):
            return None
        selector = chosen.get("selector")
        if selector:
            return str(selector)
        index = chosen.get("index")
        if isinstance(index, int) and index >= 0:
            for element in elements:
                if element.get("i") == index and element.get("selector"):
                    return str(element["selector"])
        return None

    async def _locate(self, intent: str) -> tuple[float, float]:
        if not self.ai:
            raise LocatorError("AI gateway not configured for visual self-healing")
        png = await self.driver.screenshot()
        located = await self.ai.locate(png, intent)
        if not located or located.get("x") is None or located.get("y") is None:
            raise LocatorError(f"AI could not locate target: {intent}")
        return float(located["x"]), float(located["y"])

    async def _extract_text(self, step: Step) -> None:
        if step.fields:
            record: Dict[str, Any] = {}
            for field, selector in step.fields.items():
                record[field] = await self.driver.extract_text(self._sub(selector))
            value: Any = record
        else:
            value = await self.driver.extract_text(self._sub(step.selector) or "")
        if step.save_as:
            self.vars[step.save_as] = value

    async def _ai_extract(self, step: Step) -> None:
        if not self.ai:
            raise LocatorError("ai_extract requires an AI gateway")
        text = await self.driver.page_text()
        data = await self.ai.extract(
            text=text,
            schema_ref=step.schema_ref,
            fields=step.fields,
        )
        if step.save_as:
            self.vars[step.save_as] = data

    async def _save_db(self, step: Step) -> None:
        if not self.db:
            raise ValueError("save_db requires a db_writer")
        source_key = step.source or step.save_as
        rows = self.vars.get(source_key) if source_key else None
        await self.db.write(step.table or "rpa_results", rows)

    # -- helpers ------------------------------------------------------------
    async def _capture_snapshot(self, index: int, step: Step, exc: Exception) -> Optional[dict]:
        if not self.snapshot_sink:
            return None
        try:
            png = await self.driver.screenshot()
            html = await self.driver.content()
            return await self.snapshot_sink.save(
                index=index, step=step.describe(), error=str(exc), png=png, html=html
            )
        except Exception:  # noqa: BLE001 - snapshotting must never mask the real error
            return None

    def _sub(self, value: Any) -> Any:
        return substitute(value, self.env, self.vars)
