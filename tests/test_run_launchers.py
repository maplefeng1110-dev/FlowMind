import run_agent
import run_stable


class DummyProcess:
    def __init__(self, pid: int, poll_result=None):
        self.pid = pid
        self._poll_result = poll_result
        self.terminated = False

    def poll(self):
        return self._poll_result

    def terminate(self):
        self.terminated = True


def test_run_stable_starts_registry_and_web_only(monkeypatch):
    launched = []
    cleaned_ports = []
    terminated = []
    sleep_calls = {"count": 0}

    run_stable.processes.clear()
    monkeypatch.setattr(run_stable, "find_python", lambda: "/tmp/python")
    monkeypatch.setattr(run_stable, "cleanup_ports", lambda ports: cleaned_ports.append(list(ports)))

    def fake_launch(name, args):
        launched.append((name, args))
        return DummyProcess(pid=len(launched))

    def fake_sleep(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(run_stable, "launch_process", fake_launch)
    monkeypatch.setattr(run_stable, "terminate_processes", lambda: terminated.append(True))
    monkeypatch.setattr(run_stable.time, "sleep", fake_sleep)

    run_stable.main()

    assert cleaned_ports == [[8000, 5173]]
    assert [name for name, _ in launched] == ["Registry", "Web Server"]
    assert all("agent.main" not in args for _, args in launched)
    assert terminated == [True]


def test_run_agent_restarts_agent_when_it_exits(monkeypatch):
    launched = []
    terminated = []

    run_agent.processes.clear()
    monkeypatch.setattr(run_agent, "find_python", lambda: "/tmp/python")
    monkeypatch.setattr(run_agent, "should_start_agent_admin_api", lambda: False)

    process_queue = [
        DummyProcess(pid=1, poll_result=1),
        DummyProcess(pid=2, poll_result=None),
    ]

    def fake_launch(name, args):
        launched.append((name, args))
        return process_queue.pop(0)

    monkeypatch.setattr(run_agent, "launch_process", fake_launch)
    monkeypatch.setattr(run_agent, "terminate_processes", lambda: terminated.append(True))
    monkeypatch.setattr(run_agent.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    run_agent.main()

    assert [name for name, _ in launched] == ["Agent", "Agent"]
    assert all(args == ["/tmp/python", "-m", "agent.main"] for _, args in launched)
    assert terminated == [True]


def test_run_agent_starts_agent_and_admin_api_when_enabled(monkeypatch):
    launched = []
    terminated = []

    run_agent.processes.clear()
    monkeypatch.setattr(run_agent, "find_python", lambda: "/tmp/python")
    monkeypatch.setattr(run_agent, "should_start_agent_admin_api", lambda: True)

    def fake_launch(name, args):
        launched.append((name, args))
        return DummyProcess(pid=len(launched), poll_result=None)

    monkeypatch.setattr(run_agent, "launch_process", fake_launch)
    monkeypatch.setattr(run_agent, "terminate_processes", lambda: terminated.append(True))
    monkeypatch.setattr(run_agent.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))

    run_agent.main()

    assert [name for name, _ in launched] == ["Agent", "Agent Admin API"]
    assert launched[0][1] == ["/tmp/python", "-m", "agent.main"]
    assert launched[1][1] == ["/tmp/python", "-m", "agent.admin_server"]
    assert terminated == [True]
