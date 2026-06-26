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


def run_async(coro):
    return asyncio.run(coro)


async def _browser_client(url, dom, ready):
    async with websockets.connect(url) as ws:
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
        transport = BridgeServerTransport(port=0)
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
