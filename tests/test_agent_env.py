import os

from agent.env import get_agent_env, load_agent_env


def test_load_agent_env_reads_values_from_agent_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "REGISTRY_URL=ws://127.0.0.1:9000/ws\n"
        "MACHINE_ID=agent-test-machine\n"
        "AGENT_HEARTBEAT_INTERVAL_SEC=15\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("REGISTRY_URL", raising=False)
    monkeypatch.delenv("MACHINE_ID", raising=False)
    monkeypatch.delenv("AGENT_HEARTBEAT_INTERVAL_SEC", raising=False)

    load_agent_env(env_path)

    assert os.getenv("REGISTRY_URL") == "ws://127.0.0.1:9000/ws"
    assert os.getenv("MACHINE_ID") == "agent-test-machine"
    assert os.getenv("AGENT_HEARTBEAT_INTERVAL_SEC") == "15"


def test_get_agent_env_prefers_agent_env_file_over_existing_process_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "REGISTRY_URL=ws://127.0.0.1:9000/ws\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REGISTRY_URL", "ws://127.0.0.1:8000/ws")

    assert get_agent_env("REGISTRY_URL", env_path=env_path) == "ws://127.0.0.1:9000/ws"
