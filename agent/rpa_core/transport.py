"""Command transport between the worker (ExtensionDriver) and the browser.

Topology (commercial-RPA style):

    ExtensionDriver  <--WS-->  Native Messaging Host  <--stdin/stdout-->  Extension
       (worker)              (browser_bridge/native_host)              (background+content)

The worker hosts a local WebSocket *server*; the Native Messaging Host (spawned by
the browser when the extension calls ``connectNative``) connects to it as a client
and relays JSON commands to/from the page's content script. Messages are correlated
by an ``id`` field.

``ScriptedTransport`` emulates the browser in-process for unit tests (no WebSocket,
no browser).
"""
from __future__ import annotations

import abc
import asyncio
import json
import os
import secrets
from typing import Any, Callable, Dict, Optional

TOKEN_ENV = "FLOWMIND_BRIDGE_TOKEN"
TOKEN_FILE_ENV = "FLOWMIND_BRIDGE_TOKEN_FILE"
DEFAULT_TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".flowmind", "bridge_token")
HELLO_TIMEOUT_SECONDS = 5.0


def load_or_create_bridge_token(path: Optional[str] = None) -> str:
    """Shared secret the Native Messaging Host must present on connect.

    Taken from ``FLOWMIND_BRIDGE_TOKEN`` if set, otherwise from a per-user file
    (created with 0600 permissions on first use). The browser launches the native host
    as the same OS user, so it reads the same file without extra configuration.
    """
    explicit = str(os.getenv(TOKEN_ENV, "") or "").strip()
    if explicit:
        return explicit
    token_path = path or os.getenv(TOKEN_FILE_ENV) or DEFAULT_TOKEN_FILE
    try:
        with open(token_path, encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(token_path) or ".", mode=0o700, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


class CommandTransport(abc.ABC):
    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def request(self, message: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class BridgeServerTransport(CommandTransport):
    """Local WebSocket server the Native Messaging Host connects back to."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8777,
        connect_timeout: float = 60.0,
        token: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.actual_port = port
        self.token = token
        self._server = None
        self._conn = None
        self._connected = asyncio.Event()
        self._pending: Dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        import websockets

        if self._server is not None:
            return
        if not self.token:
            self.token = load_or_create_bridge_token()
        # origins=[None]: only accept clients that send no Origin header. The native
        # host never sends one; every connection opened from a web page does.
        self._server = await websockets.serve(self._handler, self.host, self.port, origins=[None])
        try:
            self.actual_port = self._server.sockets[0].getsockname()[1]
        except Exception:  # noqa: BLE001
            self.actual_port = self.port

    async def _authenticate(self, websocket) -> bool:
        """The first frame must be ``{"type": "hello", "token": <bridge token>}``."""
        try:
            hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=HELLO_TIMEOUT_SECONDS))
        except Exception:  # noqa: BLE001 - timeout, malformed frame or early close
            hello = None
        token = hello.get("token") if isinstance(hello, dict) and hello.get("type") == "hello" else None
        if isinstance(token, str) and token and secrets.compare_digest(token.encode(), self.token.encode()):
            return True
        try:
            await websocket.close(code=1008, reason="bridge authentication failed")
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _handler(self, websocket, *_) -> None:
        # A single authenticated bridge connection is expected (the native host); a
        # reconnecting host replaces a stale one.
        # (*_ tolerates the legacy 2-arg websockets handler signature.)
        if not await self._authenticate(websocket):
            return
        self._conn = websocket
        self._connected.set()
        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except Exception:  # noqa: BLE001 - ignore malformed frames
                    continue
                future = self._pending.pop(message.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(message)
        finally:
            if self._conn is websocket:
                self._conn = None
                self._connected.clear()

    async def request(self, message: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=min(timeout, self.connect_timeout))
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "browser bridge not connected (extension / native host offline)"
            ) from exc

        future = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        await self._conn.send(json.dumps(message))
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message["id"], None)
            raise RuntimeError(f"browser command timed out: {message.get('action')}") from exc

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._server = None
        self._connected.clear()


class ScriptedTransport(CommandTransport):
    """In-process transport for tests: ``responder(message) -> response dict``."""

    def __init__(self, responder: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.responder = responder
        self.sent: list = []

    async def start(self) -> None:
        pass

    async def request(self, message: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        self.sent.append(message)
        response = dict(self.responder(message))
        response.setdefault("id", message.get("id"))
        return response

    async def close(self) -> None:
        pass
