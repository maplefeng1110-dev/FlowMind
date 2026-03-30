import asyncio
import contextlib
import json
import platform
import sys
from pathlib import Path

import websockets

from agent.env import AGENT_ENV_PATH, get_agent_env, load_agent_env
from utils.logger import setup_logger

logger = setup_logger("Agent", "agent.log")

# Add the agent directory to path for plugin imports
agent_dir = Path(__file__).parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

load_agent_env()
REGISTRY_URL = get_agent_env('REGISTRY_URL', 'ws://127.0.0.1:8000/ws')
MACHINE_ID = get_agent_env('MACHINE_ID', 'local-dev')
HEARTBEAT_INTERVAL_SEC = max(2.0, float(get_agent_env('AGENT_HEARTBEAT_INTERVAL_SEC', '10')))

if AGENT_ENV_PATH.exists():
    logger.info(f"Loaded Agent environment from {AGENT_ENV_PATH}")
else:
    logger.info(f"Agent environment file not found at {AGENT_ENV_PATH}, using process env/defaults")

from executor import LocalExecutor

executor = LocalExecutor(agent_dir / 'plugins')


def _is_enabled(raw_value: str | None) -> bool:
    return str(raw_value or "").strip().lower() not in {"0", "false", "no", "off"}


def _get_agent_admin_public_url() -> str:
    return str(get_agent_env("AGENT_ADMIN_PUBLIC_URL", "") or "").strip().rstrip("/")


def _get_agent_ws_token() -> str:
    return str(get_agent_env("FLOWMIND_AGENT_WS_TOKEN", "") or "").strip()


def _build_system_info() -> dict:
    agent_admin = {"enabled": _is_enabled(get_agent_env("AGENT_ADMIN_ENABLED", "true"))}
    public_url = _get_agent_admin_public_url()
    if public_url:
        agent_admin["api_url"] = public_url

    return {
        "os": platform.system() or "unknown",
        "hostname": platform.node() or MACHINE_ID,
        "python_version": platform.python_version(),
        "agent_admin": agent_admin,
    }


def _build_register_payload() -> dict:
    payload = {
        'type': 'register',
        'machine_id': MACHINE_ID,
        'system_info': _build_system_info(),
        'rpas': list(executor.plugins.keys()),
        'manifests': list(executor.manifests.values()),
    }
    auth_token = _get_agent_ws_token()
    if auth_token:
        payload["auth_token"] = auth_token
    return payload


async def _send_json(ws, payload: dict, send_lock: asyncio.Lock):
    """串行发送 websocket 消息，避免心跳和结果并发发送冲突。"""

    async with send_lock:
        await ws.send(json.dumps(payload))


async def _heartbeat_loop(ws, send_lock: asyncio.Lock):
    """定时向 Registry 汇报存活状态。"""

    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        try:
            await _send_json(ws, {'type': 'heartbeat'}, send_lock)
        except Exception as exc:
            logger.warning(f"Heartbeat stopped due to websocket error: {exc}")
            return


async def run_agent():
    if not _get_agent_ws_token():
        logger.warning("FLOWMIND_AGENT_WS_TOKEN is not configured; registry websocket authentication will fail.")
    while True:  # 断线自动重连
        try:
            logger.info(f"Connecting to Registry: {REGISTRY_URL}")
            async with websockets.connect(REGISTRY_URL, open_timeout=10, ping_interval=20) as ws:
                send_lock = asyncio.Lock()
                # 注册本机
                register_payload = _build_register_payload()
                plugins = register_payload["rpas"]
                await _send_json(ws, register_payload, send_lock)
                logger.info(f"Registered on machine {MACHINE_ID} with plugins: {plugins}")
                heartbeat_task = asyncio.create_task(_heartbeat_loop(ws, send_lock))

                try:
                    # 监听任务
                    async for raw in ws:
                        task = json.loads(raw)
                        logger.info(f"Received task: {task['rpa_id']} (ID: {task['task_id'][:8]})")
                        result = await executor.run(task['rpa_id'], task['params'])
                        await _send_json(ws, {
                            'type': 'result',
                            'task_id': task['task_id'],
                            'result': result
                        }, send_lock)
                        logger.info(f"Task completed with status: {result['status']}")
                finally:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task

        except Exception as e:
            logger.error(f"Agent connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(run_agent())
