"""The Agent executor refuses path parameters outside a plugin's allowed roots."""
import asyncio
import os
import types

import pytest

from agent.executor import LocalExecutor


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOWMIND_AGENT_ALLOWED_PATHS", raising=False)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "in.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("no", encoding="utf-8")

    calls = []

    async def run(**params):
        calls.append(params)
        return {"status": "success", "data": params}

    executor = LocalExecutor(tmp_path / "no-plugins")
    executor.plugins["files"] = types.SimpleNamespace(run=run)
    executor.manifests["files"] = {"id": "files", "tool_profile": {"allowed_path_roots": [str(allowed)]}}
    executor.plugins["open"] = types.SimpleNamespace(run=run)
    executor.manifests["open"] = {"id": "open"}
    return executor, allowed, outside, calls


def _run(executor, rpa_id, params):
    return asyncio.run(executor.run(rpa_id, params))


def test_path_inside_allowed_root_runs(setup):
    executor, allowed, _outside, calls = setup
    result = _run(executor, "files", {"file_path": str(allowed / "in.txt"), "output_path": str(allowed / "out.xlsx")})
    assert result["status"] == "success"
    assert len(calls) == 1


@pytest.mark.parametrize("make_path", [
    lambda allowed, outside: str(outside / "secret.txt"),
    lambda allowed, outside: str(allowed / ".." / "outside" / "secret.txt"),
])
def test_path_outside_allowed_roots_is_refused(setup, make_path):
    executor, allowed, outside, calls = setup
    result = _run(executor, "files", {"file_path": make_path(allowed, outside)})
    assert result["status"] == "error"
    assert "file_path" in result["error"]
    assert calls == []


def test_symlink_escaping_the_root_is_refused(setup):
    executor, allowed, outside, calls = setup
    link = allowed / "link.txt"
    os.symlink(outside / "secret.txt", link)
    assert _run(executor, "files", {"file_path": str(link)})["status"] == "error"
    assert calls == []


def test_list_of_paths_is_checked_item_by_item(setup):
    executor, allowed, outside, calls = setup
    params = {"source_files": [str(allowed / "in.txt"), str(outside / "secret.txt")]}
    assert _run(executor, "files", params)["status"] == "error"
    assert calls == []


def test_non_path_params_and_undeclared_plugins_are_untouched(setup):
    executor, _allowed, outside, calls = setup
    assert _run(executor, "files", {"query": str(outside / "secret.txt")})["status"] == "success"
    assert _run(executor, "open", {"file_path": str(outside / "secret.txt")})["status"] == "success"
    assert len(calls) == 2


def test_extra_roots_from_environment(setup, monkeypatch):
    executor, _allowed, outside, calls = setup
    monkeypatch.setenv("FLOWMIND_AGENT_ALLOWED_PATHS", str(outside))
    assert _run(executor, "files", {"file_path": str(outside / "secret.txt")})["status"] == "success"
    assert len(calls) == 1
