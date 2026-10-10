#!/usr/bin/env python3
"""FlowMind Agent — standalone executable entry (CLI + config file).

Packaged with PyInstaller into a single executable for Windows / macOS:

* First run interactively creates ``flowmind-agent.env`` next to the executable
  (Registry WS URL / machine id / shared token); later runs just read it.
* Connects to the Registry, loads the plugins, and runs; optionally starts the
  Agent admin API in a background thread.
* ``--native-host`` runs the browser-RPA Native Messaging Host (launched by the
  browser, not by the user).

Run from source for testing:  ``python packaging/flowmind_agent.py``
"""
import os
import socket
import sys
import threading
from pathlib import Path

CONFIG_NAME = "flowmind-agent.env"


def _bundle_root() -> Path:
    # Where bundled source (agent/, utils/, browser_bridge/) lives.
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def _app_dir() -> Path:
    # Persistent dir for the user's config: the executable's folder (frozen) or repo root.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _bundle_root()


def _setup_paths() -> None:
    root = _bundle_root()
    # agent/ modules import each other by top-level name (executor, plugins.*),
    # so both the bundle root and the agent/ dir must be importable.
    for path in (str(root), str(root / "agent")):
        if path not in sys.path:
            sys.path.insert(0, path)


def _load_env_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _prompt_config(path: Path) -> None:
    print("=" * 56)
    print(" FlowMind Agent · 首次运行配置")
    print("=" * 56)
    default_ws = "ws://127.0.0.1:8000/ws"
    registry = input(f"Registry WS 地址 [{default_ws}]: ").strip() or default_ws
    default_id = socket.gethostname() or "flowmind-agent"
    machine = input(f"机器 ID [{default_id}]: ").strip() or default_id
    token = ""
    while not token:
        token = input("Agent WS Token（需与后端 FLOWMIND_AGENT_WS_TOKEN 一致）: ").strip()
    path.write_text(
        "\n".join(
            [
                f"REGISTRY_URL={registry}",
                f"MACHINE_ID={machine}",
                f"FLOWMIND_AGENT_WS_TOKEN={token}",
                "AGENT_ADMIN_ENABLED=true",
                "AGENT_ADMIN_HOST=127.0.0.1",
                "AGENT_ADMIN_PORT=8765",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"\n已保存配置到：{path}\n（以后可直接编辑此文件修改）\n")


def _ensure_config() -> None:
    cfg = _app_dir() / CONFIG_NAME
    if not cfg.exists():
        _prompt_config(cfg)
    _load_env_file(cfg)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "off"}


def _start_admin_api() -> None:
    if not _truthy(os.getenv("AGENT_ADMIN_ENABLED", "true")):
        return
    try:
        import uvicorn

        from agent.admin_server import app as admin_app
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Agent 管理 API 未启动：{exc}")
        return
    host = os.getenv("AGENT_ADMIN_HOST", "127.0.0.1")
    port = int(os.getenv("AGENT_ADMIN_PORT", "8765"))
    config = uvicorn.Config(admin_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # not the main thread
    threading.Thread(target=server.run, daemon=True).start()
    print(f"Agent 管理 API: http://{host}:{port}")


def _run_native_host() -> None:
    import runpy

    host = _bundle_root() / "browser_bridge" / "native_host" / "flowmind_host.py"
    runpy.run_path(str(host), run_name="__main__")


def main() -> None:
    _setup_paths()

    if "--native-host" in sys.argv[1:]:
        _run_native_host()
        return

    _ensure_config()
    _start_admin_api()

    import asyncio

    from agent.main import run_agent

    print(f"连接后端：{os.getenv('REGISTRY_URL')}  ·  机器ID：{os.getenv('MACHINE_ID')}")
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
