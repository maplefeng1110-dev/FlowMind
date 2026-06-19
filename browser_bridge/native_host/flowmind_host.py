#!/usr/bin/env python3
"""FlowMind Native Messaging Host (persistent, self-reconnecting bridge).

Spawned by the browser via chrome.runtime.connectNative('com.flowmind.host'), it
relays JSON between the extension (stdin/stdout, native-messaging framing) and the
worker's local WebSocket bridge.

The worker's WS server comes and goes per flow run, so this host RETRIES the
WebSocket connection and stays alive as long as the extension keeps the native port
open (until the browser closes it). That keeps the extension<->host link stable (no
service-worker churn / console spam) while the host<->worker link reconnects on
demand.

Message direction:
  * commands  : worker --(ws)--> host --(stdout)--> extension
  * responses : extension --(stdin)--> host --(ws)--> worker
"""
import asyncio
import json
import os
import queue
import struct
import sys
import tempfile
import threading
import time

BRIDGE_URL = os.getenv("FLOWMIND_BROWSER_BRIDGE_URL", "ws://127.0.0.1:8777")
LOG_PATH = os.getenv("FLOWMIND_HOST_LOG", os.path.join(tempfile.gettempdir(), "flowmind_host.log"))
RETRY_SECONDS = 1.5


def _log(message):
    try:
        with open(LOG_PATH, "a") as handle:
            handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), message))
    except Exception:  # noqa: BLE001
        pass


def read_message():
    """Read one native-messaging frame from stdin (4-byte length + JSON)."""
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("@I", raw_len)
    data = sys.stdin.buffer.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def write_message(obj):
    """Write one native-messaging frame to stdout."""
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("@I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


_INBOX = queue.Queue()  # responses from the extension (stdin)
_EOF = object()         # sentinel: the browser closed the native port


def _stdin_reader():
    while True:
        message = read_message()
        if message is None:
            _INBOX.put(_EOF)
            return
        _INBOX.put(message)


def _inbox_get():
    # Short timeout so the consumer stays cancellable when the ws drops.
    try:
        return _INBOX.get(timeout=0.3)
    except queue.Empty:
        return None


class _HostExit(Exception):
    """Raised when the browser closes the native port -> exit the host."""


async def _to_worker(ws, loop, stop):
    while not stop.is_set():
        item = await loop.run_in_executor(None, _inbox_get)
        if item is None:
            continue
        if item is _EOF:
            raise _HostExit()
        await ws.send(json.dumps(item))


async def _to_extension(ws, loop):
    async for raw in ws:
        try:
            obj = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        await loop.run_in_executor(None, write_message, obj)


async def main():
    import websockets

    loop = asyncio.get_event_loop()
    threading.Thread(target=_stdin_reader, daemon=True).start()
    _log("host started; worker=%s" % BRIDGE_URL)

    offline_announced = False
    while True:
        try:
            async with websockets.connect(BRIDGE_URL) as ws:
                _log("connected to worker bridge")
                offline_announced = False
                stop = asyncio.Event()
                to_worker = asyncio.ensure_future(_to_worker(ws, loop, stop))
                to_extension = asyncio.ensure_future(_to_extension(ws, loop))
                done, pending = await asyncio.wait(
                    {to_worker, to_extension}, return_when=asyncio.FIRST_COMPLETED
                )
                stop.set()
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except Exception:  # noqa: BLE001
                        pass
                for task in done:
                    if isinstance(task.exception(), _HostExit):
                        _log("native port closed; host exiting")
                        return
                # Otherwise the worker ws closed -> fall through and reconnect.
        except _HostExit:
            return
        except (OSError, ConnectionError):
            if not offline_announced:
                _log("worker offline; retrying every %ss" % RETRY_SECONDS)
                offline_announced = True
        except Exception as exc:  # noqa: BLE001
            _log("error: %s" % exc)
        await asyncio.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        _log("fatal: %s" % exc)
        sys.exit(1)
