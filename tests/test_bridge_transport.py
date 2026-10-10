"""Loopback test for BridgeServerTransport over a real local WebSocket.

Validates the worker-side bridge plumbing (server hosting, id correlation,
send/recv) by connecting a client that emulates the native host + browser.
"""
import asyncio
import json

import pytest

from agent.rpa_core.driver import ExtensionDriver
from agent.rpa_core.flow_dsl import load_flow
from agent.rpa_core.interpreter import FlowRunner
from agent.rpa_core.transport import BridgeServerTransport

websockets = pytest.importorskip("websockets")

TOKEN = "bridge-token-for-tests"


def run_async(coro):
    return asyncio.run(coro)


async def _browser_client(url, dom, ready, token=TOKEN):
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "hello", "token": token}))
        ready.set()
        async for raw in ws:
            msg = json.loads(raw)
            action = msg["action"]
            if action == "extract_text":
                value = dom.get(msg["selector"], [])
                resp = {"ok": True, "result": value if isinstance(value, list) else [value]}
            elif action in ("goto", "click", "type"):
                resp = {"ok": True, "result": {"url": msg.get("url")}}
            else:
                resp = {"ok": True, "result": None}
            resp["id"] = msg["id"]
            await ws.send(json.dumps(resp))


def test_bridge_server_transport_loopback():
    async def scenario():
        transport = BridgeServerTransport(port=0, token=TOKEN)
        await transport.start()
        url = "ws://127.0.0.1:%d" % transport.actual_port
        ready = asyncio.Event()
        dom = {"#orders .order": ["A", "B"]}
        client = asyncio.ensure_future(_browser_client(url, dom, ready))
        await asyncio.wait_for(ready.wait(), timeout=5)

        flow = load_flow(
            flow={
                "steps": [
                    {"action": "goto", "url": "http://h"},
                    {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
                ]
            }
        )
        try:
            return await FlowRunner(ExtensionDriver(transport), env={}).run(flow)
        finally:
            client.cancel()
            await transport.close()

    result = run_async(scenario())
    assert result["status"] == "success"
    assert result["vars"]["orders"] == ["A", "B"]


def test_bridge_rejects_wrong_token():
    async def scenario():
        transport = BridgeServerTransport(port=0, token=TOKEN)
        await transport.start()
        url = "ws://127.0.0.1:%d" % transport.actual_port
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({"type": "hello", "token": "wrong"}))
                with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                    await asyncio.wait_for(ws.recv(), timeout=5)
            return closed.value.rcvd.code, transport._connected.is_set()
        finally:
            await transport.close()

    code, connected = run_async(scenario())
    assert code == 1008
    assert connected is False


def test_bridge_rejects_connections_with_an_origin():
    async def scenario():
        transport = BridgeServerTransport(port=0, token=TOKEN)
        await transport.start()
        url = "ws://127.0.0.1:%d" % transport.actual_port
        try:
            with pytest.raises(websockets.exceptions.InvalidHandshake):
                async with websockets.connect(url, origin="https://example.com"):
                    pass
            return transport._connected.is_set()
        finally:
            await transport.close()

    assert run_async(scenario()) is False


def test_bridge_token_file_is_created_private_and_reused(tmp_path, monkeypatch):
    from agent.rpa_core.transport import load_or_create_bridge_token

    monkeypatch.delenv("FLOWMIND_BRIDGE_TOKEN", raising=False)
    path = tmp_path / "nested" / "bridge_token"
    first = load_or_create_bridge_token(str(path))
    assert first and load_or_create_bridge_token(str(path)) == first
    assert (path.stat().st_mode & 0o777) == 0o600
    monkeypatch.setenv("FLOWMIND_BRIDGE_TOKEN", "from-env")
    assert load_or_create_bridge_token(str(path)) == "from-env"


def test_real_native_host_authenticates_and_relays(tmp_path):
    """Spawn browser_bridge/native_host/flowmind_host.py and relay one command through it."""
    import os
    import struct
    import subprocess
    import sys
    from pathlib import Path

    host_script = Path(__file__).resolve().parents[1] / "browser_bridge" / "native_host" / "flowmind_host.py"
    token_file = tmp_path / "bridge_token"
    token_file.write_text("host-token", encoding="utf-8")

    async def scenario():
        loop = asyncio.get_running_loop()
        transport = BridgeServerTransport(port=0, token="host-token")
        await transport.start()
        env = dict(os.environ)
        env.pop("FLOWMIND_BRIDGE_TOKEN", None)
        env.update({
            "FLOWMIND_BRIDGE_TOKEN_FILE": str(token_file),
            "FLOWMIND_BROWSER_BRIDGE_URL": "ws://127.0.0.1:%d" % transport.actual_port,
            "FLOWMIND_HOST_LOG": str(tmp_path / "host.log"),
        })
        proc = subprocess.Popen([sys.executable, str(host_script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        try:
            await asyncio.wait_for(transport._connected.wait(), timeout=15)
            pending = asyncio.ensure_future(transport.request({"id": "c1", "action": "ping"}, timeout=10))
            (length,) = struct.unpack("@I", await loop.run_in_executor(None, proc.stdout.read, 4))
            command = json.loads(await loop.run_in_executor(None, proc.stdout.read, length))
            reply = json.dumps({"id": command["id"], "ok": True, "result": "pong"}).encode()
            proc.stdin.write(struct.pack("@I", len(reply)) + reply)
            proc.stdin.flush()
            return command, await pending
        finally:
            proc.kill()
            proc.wait(timeout=5)
            await transport.close()

    command, response = run_async(scenario())
    assert command["action"] == "ping"
    assert response["ok"] is True and response["result"] == "pong"
