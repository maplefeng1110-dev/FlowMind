import hmac
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID, uuid4

import anyio
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import ValidationError
from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


class SessionBindingError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SessionBoundSseTransport:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._read_stream_writers: dict[UUID, MemoryObjectSendStream[types.JSONRPCMessage | Exception]] = {}
        self._session_tokens: dict[str, str] = {}

    def validate_session_binding(self, session_id_param: Optional[str], session_token: Optional[str]) -> UUID:
        if session_id_param is None:
            raise SessionBindingError(status_code=400, detail="session_id is required")

        try:
            session_id = UUID(hex=session_id_param)
        except ValueError as exc:
            raise SessionBindingError(status_code=400, detail="Invalid session ID") from exc

        bound_session_token = self._session_tokens.get(session_id.hex)
        if not bound_session_token:
            raise SessionBindingError(status_code=404, detail="Could not find session")
        if not session_token:
            raise SessionBindingError(status_code=401, detail="Missing or invalid user session")
        if not hmac.compare_digest(bound_session_token, str(session_token)):
            raise SessionBindingError(
                status_code=401,
                detail="MCP session does not belong to current user session",
            )
        return session_id

    @asynccontextmanager
    async def connect_sse(self, scope: Scope, receive: Receive, send: Send, *, session_token: str):
        if scope["type"] != "http":
            raise ValueError("connect_sse can only handle HTTP requests")

        read_stream: MemoryObjectReceiveStream[types.JSONRPCMessage | Exception]
        read_stream_writer: MemoryObjectSendStream[types.JSONRPCMessage | Exception]
        write_stream: MemoryObjectSendStream[types.JSONRPCMessage]
        write_stream_reader: MemoryObjectReceiveStream[types.JSONRPCMessage]

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        session_uri = f"{quote(self._endpoint)}?session_id={session_id.hex}"
        self._read_stream_writers[session_id] = read_stream_writer
        self._session_tokens[session_id.hex] = str(session_token)

        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream[dict[str, Any]](0)

        async def sse_writer():
            async with sse_stream_writer, write_stream_reader:
                await sse_stream_writer.send({"event": "endpoint", "data": session_uri})
                async for message in write_stream_reader:
                    await sse_stream_writer.send(
                        {
                            "event": "message",
                            "data": message.model_dump_json(by_alias=True, exclude_none=True),
                        }
                    )

        async with anyio.create_task_group() as tg:
            response = EventSourceResponse(content=sse_stream_reader, data_sender_callable=sse_writer)
            tg.start_soon(response, scope, receive, send)
            try:
                yield (read_stream, write_stream)
            finally:
                self._session_tokens.pop(session_id.hex, None)
                self._read_stream_writers.pop(session_id, None)
                with suppress(Exception):
                    await read_stream_writer.aclose()
                with suppress(Exception):
                    await write_stream.aclose()

    async def handle_post_message(self, scope: Scope, receive: Receive, send: Send, *, session_token: str) -> None:
        request = Request(scope, receive)
        try:
            session_id = self.validate_session_binding(request.query_params.get("session_id"), session_token)
        except SessionBindingError as exc:
            response = Response(exc.detail, status_code=exc.status_code)
            return await response(scope, receive, send)

        writer = self._read_stream_writers.get(session_id)
        if not writer:
            response = Response("Could not find session", status_code=404)
            return await response(scope, receive, send)

        try:
            payload = await request.json()
        except Exception:
            response = Response("Could not parse message", status_code=400)
            return await response(scope, receive, send)

        try:
            message = types.JSONRPCMessage.model_validate(payload)
        except ValidationError as err:
            response = Response("Could not parse message", status_code=400)
            await response(scope, receive, send)
            await writer.send(err)
            return

        response = Response("Accepted", status_code=202)
        await response(scope, receive, send)
        await writer.send(message)
