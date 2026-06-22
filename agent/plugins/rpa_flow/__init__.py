"""rpa_flow plugin: execute a YAML/JSON RPA flow via a browser extension bridge.

The worker drives an already-running browser through a browser extension + Native
Messaging Host (see ``browser_bridge/``), so there is no CDP/Playwright and no
separate browser launch — it takes over the user's current tab and manipulates the
DOM with native JS. AI visual self-healing (``ai_click``) and structured extraction
(``ai_extract``) route through the Orchestrator AI gateway.

Exposes ``async run(**params)`` per the FlowMind plugin contract.
"""
from __future__ import annotations

import os
from typing import Optional

from utils.plugin_result import error_result, success_result

from agent.rpa_core.ai_gateway import AIGatewayClient
from agent.rpa_core.driver import ExtensionDriver
from agent.rpa_core.flow_dsl import load_flow
from agent.rpa_core.interpreter import FlowRunner
from agent.rpa_core.results_db import SqliteResultWriter
from agent.rpa_core.snapshots import LocalSnapshotSink, RemoteSnapshotSink, RemoteSnapshotSink
from agent.rpa_core.transport import BridgeServerTransport


def _bridge_port(explicit: Optional[int]) -> int:
    if explicit is not None:
        return int(explicit)
    try:
        return int(os.getenv("FLOWMIND_BROWSER_BRIDGE_PORT", "8777"))
    except (TypeError, ValueError):
        return 8777


async def run(
    flow_path: Optional[str] = None,
    flow: Optional[dict] = None,
    env: Optional[dict] = None,
    gateway_url: Optional[str] = None,
    db_path: Optional[str] = None,
    bridge_host: Optional[str] = None,
    bridge_port: Optional[int] = None,
) -> dict:
    try:
        parsed = load_flow(flow_path=flow_path, flow=flow)
    except Exception as exc:  # noqa: BLE001
        return error_result("Invalid flow definition", error=str(exc))

    transport = BridgeServerTransport(
        host=bridge_host or os.getenv("FLOWMIND_BROWSER_BRIDGE_HOST", "127.0.0.1"),
        port=_bridge_port(bridge_port),
    )
    driver = ExtensionDriver(transport)

    resolved_gateway = gateway_url or os.getenv("FLOWMIND_AI_GATEWAY_URL")
    ai = AIGatewayClient(resolved_gateway) if resolved_gateway else None
    db = SqliteResultWriter(db_path or os.getenv("RPA_RESULTS_DB", "data/rpa_results.db"))
    snapshot_dir = os.getenv("RPA_SNAPSHOT_DIR", "data/snapshots")
    artifacts_url = os.getenv("FLOWMIND_ARTIFACTS_URL")
    snapshots = (
        RemoteSnapshotSink(artifacts_url, fallback_dir=snapshot_dir)
        if artifacts_url
        else LocalSnapshotSink(snapshot_dir)
    )

    try:
        await driver.start()
    except Exception as exc:  # noqa: BLE001
        return error_result("Failed to start browser bridge", error=str(exc))

    try:
        runner = FlowRunner(
            driver,
            ai_gateway=ai,
            db_writer=db,
            env=env or {},
            snapshot_sink=snapshots,
        )
        result = await runner.run(parsed)
    finally:
        await driver.close()

    if result.get("status") == "success":
        return success_result(data=result)
    return error_result("Flow execution failed", error=result.get("error"), data=result)
