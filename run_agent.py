import subprocess
import sys
import time
from pathlib import Path

from agent.env import get_agent_env, load_agent_env


ROOT_DIR = Path(__file__).resolve().parent
processes = []


def find_python() -> str:
    candidates = [
        ROOT_DIR / ".venv" / "bin" / "python",  # Unix
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",  # Windows
        ROOT_DIR / ".venv" / "python.exe",  # Windows (legacy)
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def launch_process(name: str, args):
    process = subprocess.Popen(args, cwd=str(ROOT_DIR))
    processes.append((name, process, args))
    print(f"Started {name} (PID {process.pid})")
    return process


def terminate_processes():
    for name, process, _ in processes:
        if process.poll() is None:
            print(f"Stopping {name}...")
            process.terminate()


def _is_enabled(raw_value: str | None) -> bool:
    return str(raw_value or "").strip().lower() not in {"0", "false", "no", "off"}


def should_start_agent_admin_api() -> bool:
    load_agent_env()
    return _is_enabled(get_agent_env("AGENT_ADMIN_ENABLED", "true"))


def main():
    python_executable = find_python()
    print("--- Starting FlowMind Agent ---")
    print(f"Using Python: {python_executable}")

    managed_processes = {
        "Agent": [python_executable, "-m", "agent.main"],
    }
    if should_start_agent_admin_api():
        managed_processes["Agent Admin API"] = [python_executable, "-m", "agent.admin_server"]

    running = {name: launch_process(name, args) for name, args in managed_processes.items()}
    print(f"Started services: {', '.join(running.keys())}. Press Ctrl+C to stop.")

    try:
        while True:
            for name, args in managed_processes.items():
                if running[name].poll() is not None:
                    print(f"{name} exited unexpectedly, restarting...")
                    running[name] = launch_process(name, args)
            time.sleep(5)
    except KeyboardInterrupt:
        print("Stopping agent...")
    finally:
        terminate_processes()


if __name__ == "__main__":
    main()
