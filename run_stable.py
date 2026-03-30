import os
import signal
import subprocess
import sys
import time
from pathlib import Path


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


def find_pid_by_port(port: int):
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)",
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT_DIR))
        pid = result.stdout.strip()
        return int(pid) if pid.isdigit() else None
    else:
        # macOS/Linux
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                cwd=str(ROOT_DIR)
            )
            pid = result.stdout.strip().split('\n')[0] # Get first PID if multiple
            return int(pid) if pid.isdigit() else None
        except Exception:
            return None


def cleanup_ports(ports):
    for port in ports:
        pid = find_pid_by_port(port)
        if not pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped process on port {port} (PID {pid})")
        except OSError as exc:
            print(f"Failed to stop process on port {port}: {exc}")


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


def main():
    python_executable = find_python()
    print("--- Starting FlowMind core services ---")
    print(f"Using Python: {python_executable}")
    cleanup_ports([8000, 5173])

    registry = launch_process(
        "Registry",
        [python_executable, "-m", "uvicorn", "registry.main:app", "--host", "127.0.0.1", "--port", "8000"],
    )
    time.sleep(2)
    web = launch_process("Web Server", [python_executable, "-m", "client.web_server"])

    print("Core services started. Start Agent separately with run_agent.py. Press Ctrl+C to stop.")

    try:
        while True:
            if registry.poll() is not None:
                print("Registry exited unexpectedly.")
                break
            if web.poll() is not None:
                print("Web Server exited unexpectedly, restarting...")
                web = launch_process("Web Server", [python_executable, "-m", "client.web_server"])
            time.sleep(5)
    except KeyboardInterrupt:
        print("Stopping all services...")
    finally:
        terminate_processes()


if __name__ == "__main__":
    main()
