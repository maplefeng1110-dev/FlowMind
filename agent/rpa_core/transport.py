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
from typing import Any, Callable, Dict


class CommandTransport(abc.ABC):
    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def request(self, message: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class BridgeServerTransport(CommandTransport):
    """Local WebSocket server the Native Messaging Host connects back to."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8777, connect_timeout: float = 60.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.actual_port = port
        self._server = None
        self._conn = None
        self._connected = asyncio.Event()
        self._pending: Dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        import websockets

        if self._server is not None:
            return
        self._server = await websockets.serve(self._handler, self.host, self.port)
        try:
            self.actual_port = self._server.sockets[0].getsockname()[1]
        except Exception:  # noqa: BLE001
            self.actual_port = self.port

    async def _handler(self, websocket, *_) -> None:
        # A single bridge connection is expected (the native host). Last one wins.
        # (*_ tolerates the legacy 2-arg websockets handler signature.)
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
