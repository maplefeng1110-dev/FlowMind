"""Browser driver abstraction for rpa_flow.

The interpreter talks to an abstract :class:`BrowserDriver` so its logic (including
visual self-healing) is transport-agnostic. The production driver is
:class:`ExtensionDriver`, which controls the browser through a **browser extension +
Native Messaging Host** (commercial-RPA style) instead of CDP/Playwright: commands
are relayed to a content script that manipulates the DOM with native JS. This avoids
CDP detection, survives browser upgrades, and can take over an already-open,
already-logged-in tab.

:class:`FakeDriver` keeps the interpreter unit-testable without any browser.
"""
from __future__ import annotations

import abc
import base64
import uuid
from typing import Any, Dict, List, Optional


class LocatorError(Exception):
    """Raised when a selector cannot be resolved or acted upon.

    The interpreter treats this as the trigger for AI visual self-healing.
    """


class BrowserDriver(abc.ABC):
    @abc.abstractmethod
    async def goto(self, url: str, timeout_ms: int = 15000) -> None: ...

    @abc.abstractmethod
    async def click(self, selector: str, timeout_ms: int = 15000) -> None: ...

    @abc.abstractmethod
    async def type(self, selector: str, text: str, timeout_ms: int = 15000) -> None: ...

    @abc.abstractmethod
    async def type_text(self, text: str) -> None:
        """Type into whatever element currently has focus (used after click_xy)."""

    @abc.abstractmethod
    async def scroll(self, direction: str = "down", amount: int = 600) -> None: ...

    @abc.abstractmethod
    async def extract_text(self, selector: str, limit: int = 50) -> List[str]: ...

    @abc.abstractmethod
    async def page_text(self) -> str: ...

    @abc.abstractmethod
    async def content(self) -> str:
        """Return the current page HTML source."""

    @abc.abstractmethod
    async def screenshot(self) -> bytes: ...

    @abc.abstractmethod
    async def click_xy(self, x: float, y: float) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class ExtensionDriver(BrowserDriver):
    """Drives the browser via the extension bridge (see ``transport.py``).

    Each method sends a JSON command over the transport and awaits a correlated
    response. The browser side (background + content script) performs the actual
    DOM operation. ``{ok: false, code: "locator"}`` responses become
    :class:`LocatorError` so the interpreter can fall back to AI self-healing.
    """

    def __init__(self, transport: Any, *, default_timeout_ms: int = 15000):
        self.transport = transport
        self.default_timeout_ms = default_timeout_ms

    async def start(self) -> None:
        await self.transport.start()

    async def _command(self, action: str, *, timeout_ms: Optional[int] = None, **fields: Any) -> Any:
        message: Dict[str, Any] = {"id": uuid.uuid4().hex, "action": action}
        message.update(fields)
        # Allow extra wall-clock beyond the in-page timeout for relay round-trips.
        wait_s = ((timeout_ms or self.default_timeout_ms) / 1000.0) + 5.0
        response = await self.transport.request(message, timeout=wait_s)
        if not response.get("ok"):
            if response.get("code") == "locator":
                raise LocatorError(response.get("error", "element not found"))
            raise RuntimeError(response.get("error", f"browser command failed: {action}"))
        return response.get("result")

    async def goto(self, url: str, timeout_ms: int = 15000) -> None:
        await self._command("goto", url=url, timeout_ms=timeout_ms)

    async def click(self, selector: str, timeout_ms: int = 15000) -> None:
        await self._command("click", selector=selector, timeout_ms=timeout_ms)

    async def type(self, selector: str, text: str, timeout_ms: int = 15000) -> None:
        await self._command("type", selector=selector, text=text, timeout_ms=timeout_ms)

    async def type_text(self, text: str) -> None:
        await self._command("type_text", text=text)

    async def scroll(self, direction: str = "down", amount: int = 600) -> None:
        await self._command("scroll", direction=direction, amount=amount)

    async def extract_text(self, selector: str, limit: int = 50) -> List[str]:
        result = await self._command("extract_text", selector=selector, limit=limit)
        return list(result) if result else []

    async def page_text(self) -> str:
        return await self._command("page_text") or ""

    async def content(self) -> str:
        return await self._command("content") or ""

    async def screenshot(self) -> bytes:
        # Background captures the visible tab and returns a PNG data URL.
        result = await self._command("screenshot")
        data_url = (result or {}).get("data_url", "")
        if "," in data_url:
            return base64.b64decode(data_url.split(",", 1)[1])
        return b""

    async def click_xy(self, x: float, y: float) -> None:
        # Coordinates are in captured-image pixels; the content script maps them
        # back to CSS pixels via devicePixelRatio before elementFromPoint().
        await self._command("click_xy", x=x, y=y)

    async def close(self) -> None:
        await self.transport.close()


class FakeDriver(BrowserDriver):
    """In-memory driver for unit tests.

    ``elements`` maps a selector to a record ``{"text": str | list}``. Selectors
    listed in ``missing`` raise :class:`LocatorError` on click/type so the
    self-healing fallback path can be exercised without a real browser.
    """

    def __init__(self, elements=None, page_text: str = "", missing=None):
        self.elements: Dict[str, dict] = elements or {}
        self._page_text = page_text
        self.missing = set(missing or [])
        self.actions: List[tuple] = []
        self.current_url = "about:blank"
        self.typed: Dict[str, str] = {}

    def _exists(self, selector: str) -> bool:
        return selector in self.elements and selector not in self.missing

    async def goto(self, url: str, timeout_ms: int = 15000) -> None:
        self.current_url = url
        self.actions.append(("goto", url))

    async def click(self, selector: str, timeout_ms: int = 15000) -> None:
        if not self._exists(selector):
            raise LocatorError(f"missing selector: {selector}")
        self.actions.append(("click", selector))

    async def type(self, selector: str, text: str, timeout_ms: int = 15000) -> None:
        if not self._exists(selector):
            raise LocatorError(f"missing selector: {selector}")
        self.typed[selector] = text
        self.actions.append(("type", selector, text))

    async def type_text(self, text: str) -> None:
        self.actions.append(("type_text", text))

    async def scroll(self, direction: str = "down", amount: int = 600) -> None:
        self.actions.append(("scroll", direction, amount))

    async def extract_text(self, selector: str, limit: int = 50) -> List[str]:
        if not self._exists(selector):
            return []
        value = self.elements[selector].get("text", "")
        items = value if isinstance(value, list) else [value]
        return [str(v) for v in items][:limit]

    async def page_text(self) -> str:
        return self._page_text

    async def content(self) -> str:
        return f"<html><body>{self._page_text}</body></html>"

    async def screenshot(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake-screenshot"

    async def click_xy(self, x: float, y: float) -> None:
        self.actions.append(("click_xy", x, y))

    async def close(self) -> None:
        self.actions.append(("close",))
