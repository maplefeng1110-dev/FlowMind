"""Unit tests for the rpa_flow execution engine.

These exercise the DSL parser, the step interpreter, the visual self-healing
fallback, and the SQLite sink using an in-memory FakeDriver + FakeAIGateway, so
they run without any browser. Several tests also drive the real ExtensionDriver
over a ScriptedTransport that emulates the extension/content-script side.

Async coroutines are driven via asyncio.run() so the suite does not depend on
pytest-asyncio.
"""
import asyncio
import json
import os
import sqlite3

import pytest

from agent.rpa_core.ai_gateway import FakeAIGateway
from agent.rpa_core.driver import ExtensionDriver, FakeDriver, LocatorError
from agent.rpa_core.flow_dsl import Flow, load_flow, substitute
from agent.rpa_core.interpreter import FlowRunner
from agent.rpa_core.results_db import SqliteResultWriter, _safe_table
from agent.rpa_core.transport import ScriptedTransport


def run_async(coro):
    return asyncio.run(coro)


# -- DSL parsing & substitution --------------------------------------------
def test_load_flow_inline_and_defaults():
    flow = load_flow(flow={"name": "f", "steps": [{"action": "goto", "url": "x"}]})
    assert isinstance(flow, Flow)
    assert flow.steps[0].action == "goto"
    assert flow.context.incognito is True  # default applied


def test_step_from_alias_is_accepted():
    flow = load_flow(flow={"steps": [{"action": "save_db", "table": "t", "from": "orders"}]})
    assert flow.steps[0].source == "orders"


def test_substitute_env_and_vars():
    out = substitute("${env.base_url}/a/${vars.id}", {"base_url": "http://h"}, {"id": 7})
    assert out == "http://h/a/7"
    # unknown scope is left intact
    assert substitute("${foo.bar}", {}, {}) == "${foo.bar}"


# -- basic flow execution ---------------------------------------------------
def test_basic_flow_runs_against_fake_driver():
    driver = FakeDriver(
        elements={
            "#username": {"text": ""},
            "#login-btn": {"text": "Login"},
            "#orders .order": {"text": ["A", "B", "C"]},
        }
    )
    flow = load_flow(
        flow={
            "name": "t",
            "steps": [
                {"action": "goto", "url": "${env.base_url}/login.html"},
                {"action": "type", "selector": "#username", "text": "${env.user}", "heal": False},
                {"action": "click", "selector": "#login-btn", "heal": False},
                {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
            ],
        }
    )
    result = run_async(FlowRunner(driver, env={"base_url": "http://h", "user": "alice"}).run(flow))
    assert result["status"] == "success"
    assert result["vars"]["orders"] == ["A", "B", "C"]
    assert driver.typed["#username"] == "alice"
    assert ("click", "#login-btn") in driver.actions
    assert driver.current_url == "http://h/login.html"


# -- visual self-healing ----------------------------------------------------
def test_self_heal_click_falls_back_to_ai():
    driver = FakeDriver(missing={"#login-btn"})
    gateway = FakeAIGateway(locate_map={"登录": (320, 240)})
    flow = load_flow(
        flow={
            "steps": [
                {"action": "click", "selector": "#login-btn", "intent": "点击登录按钮", "heal": True}
            ]
        }
    )
    result = run_async(FlowRunner(driver, ai_gateway=gateway, env={}).run(flow))
    assert result["status"] == "success"
    assert result["steps"][0].get("healed") is True
    assert ("click_xy", 320, 240) in driver.actions
    assert gateway.calls[0][0] == "locate"


def test_ai_click_action_always_uses_gateway():
    driver = FakeDriver()
    gateway = FakeAIGateway(locate_map={"提交": (50, 60)})
    flow = load_flow(flow={"steps": [{"action": "ai_click", "intent": "提交表单"}]})
    result = run_async(FlowRunner(driver, ai_gateway=gateway).run(flow))
    assert result["status"] == "success"
    assert ("click_xy", 50, 60) in driver.actions


# -- tiered self-healing: DOM-text pick (tier ②) before vision (tier ③) -----
def test_self_heal_click_prefers_dom_pick_over_vision():
    # #login-btn is stale; the DOM-pick tier finds the real button by text and
    # clicks its selector -- no screenshot/locate needed.
    driver = FakeDriver(
        elements={"#login-real": {"text": "登录"}},
        missing={"#login-btn"},
        interactive=[{"i": 0, "selector": "#login-real", "tag": "button", "text": "登录"}],
    )
    gateway = FakeAIGateway(pick_map={"登录": "#login-real"})
    flow = load_flow(
        flow={"steps": [{"action": "click", "selector": "#login-btn", "intent": "点击登录按钮"}]}
    )
    result = run_async(FlowRunner(driver, ai_gateway=gateway).run(flow))
    assert result["status"] == "success"
    assert result["steps"][0].get("healed") is True
    assert ("click", "#login-real") in driver.actions
    assert gateway.calls[0][0] == "pick"
    assert all(call[0] != "locate" for call in gateway.calls)  # never reached vision
    assert not any(action[0] == "click_xy" for action in driver.actions)


def test_self_heal_falls_back_to_vision_when_dom_pick_misses():
    # DOM-pick finds nothing (index -1) -> escalate to the visual coordinate tier.
    driver = FakeDriver(
        missing={"#login-btn"},
        interactive=[{"i": 0, "selector": "#other", "tag": "a", "text": "首页"}],
    )
    gateway = FakeAIGateway(locate_map={"登录": (320, 240)})  # no pick_map -> pick returns -1
    flow = load_flow(
        flow={"steps": [{"action": "click", "selector": "#login-btn", "intent": "点击登录按钮"}]}
    )
    result = run_async(FlowRunner(driver, ai_gateway=gateway).run(flow))
    assert result["status"] == "success"
    assert result["steps"][0].get("healed") is True
    assert [call[0] for call in gateway.calls] == ["pick", "locate"]  # tier ② then tier ③
    assert ("click_xy", 320, 240) in driver.actions


def test_type_self_heal_uses_dom_pick():
    driver = FakeDriver(
        elements={"#user-real": {"text": ""}},
        missing={"#user"},
        interactive=[{"i": 0, "selector": "#user-real", "tag": "input", "placeholder": "用户名"}],
    )
    gateway = FakeAIGateway(pick_map={"用户名": "#user-real"})
    flow = load_flow(
        flow={
            "steps": [
                {"action": "type", "selector": "#user", "text": "alice", "intent": "用户名输入框"}
            ]
        }
    )
    result = run_async(FlowRunner(driver, ai_gateway=gateway).run(flow))
    assert result["status"] == "success"
    assert result["steps"][0].get("healed") is True
    assert driver.typed["#user-real"] == "alice"
    assert gateway.calls[0][0] == "pick"


def test_click_without_heal_or_gateway_errors():
    driver = FakeDriver(missing={"#x"})
    flow = load_flow(flow={"steps": [{"action": "click", "selector": "#x", "heal": False}]})
    result = run_async(FlowRunner(driver).run(flow))
    assert result["status"] == "error"
    assert result["steps"][0]["status"] == "error"


def test_optional_step_failure_does_not_abort():
    driver = FakeDriver(elements={"#ok": {"text": "y"}}, missing={"#bad"})
    flow = load_flow(
        flow={
            "steps": [
                {"action": "click", "selector": "#bad", "heal": False, "optional": True},
                {"action": "click", "selector": "#ok", "heal": False},
            ]
        }
    )
    result = run_async(FlowRunner(driver).run(flow))
    assert result["status"] == "success"
    assert result["steps"][0].get("skipped") is True
    assert ("click", "#ok") in driver.actions


# -- retries & resume (gap ①) ----------------------------------------------
def test_step_retries_then_fails_with_failed_index():
    driver = FakeDriver(missing={"#x"})
    flow = load_flow(
        flow={"steps": [{"action": "click", "selector": "#x", "heal": False, "retries": 2, "retry_delay_ms": 0}]}
    )
    result = run_async(FlowRunner(driver).run(flow))
    assert result["status"] == "error"
    assert result["steps"][0]["attempts"] == 3  # 1 try + 2 retries
    assert result["failed_index"] == 0


def test_resume_from_index_with_variables():
    driver = FakeDriver(elements={"#ok": {"text": "y"}}, missing={"#missing"})
    flow = load_flow(
        flow={
            "steps": [
                {"action": "click", "selector": "#missing", "heal": False},
                {"action": "click", "selector": "#ok", "heal": False},
            ]
        }
    )
    # Resume from step 1 (skip the failing step 0), seeding accumulated vars.
    result = run_async(FlowRunner(driver).run(flow, start_index=1, variables={"seed": 1}))
    assert result["status"] == "success"
    assert result["vars"]["seed"] == 1
    assert [s["index"] for s in result["steps"]] == [1]
    assert ("click", "#ok") in driver.actions


# -- ai_extract & save_db ---------------------------------------------------
def test_ai_extract_uses_gateway_value():
    driver = FakeDriver(page_text="发票号 123 金额 99")
    gateway = FakeAIGateway(extract_value={"invoice_no": "123", "amount": 99})
    flow = load_flow(flow={"steps": [{"action": "ai_extract", "schema_ref": "invoice", "save_as": "inv"}]})
    result = run_async(FlowRunner(driver, ai_gateway=gateway).run(flow))
    assert result["status"] == "success"
    assert result["vars"]["inv"] == {"invoice_no": "123", "amount": 99}


def test_save_db_writes_rows(tmp_path):
    db_file = str(tmp_path / "r.db")
    driver = FakeDriver(elements={"#orders .order": {"text": ["A", "B"]}})
    flow = load_flow(
        flow={
            "steps": [
                {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
                {"action": "save_db", "table": "orders", "from": "orders"},
            ]
        }
    )
    result = run_async(FlowRunner(driver, db_writer=SqliteResultWriter(db_file)).run(flow))
    assert result["status"] == "success"
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT payload FROM orders").fetchall()
    conn.close()
    assert len(rows) == 2
    assert json.loads(rows[0][0]) == "A"


def test_safe_table_sanitizes_identifier():
    assert _safe_table("orders; DROP TABLE x") == "ordersDROPTABLEx"
    assert _safe_table("") == "rpa_results"


# -- failure snapshots (PRD 3.2) --------------------------------------------
def test_failure_captures_snapshot(tmp_path):
    from agent.rpa_core.snapshots import LocalSnapshotSink

    driver = FakeDriver(missing={"#x"})
    sink = LocalSnapshotSink(str(tmp_path), task_id="t1")
    flow = load_flow(flow={"steps": [{"action": "click", "selector": "#x", "heal": False}]})
    result = run_async(FlowRunner(driver, snapshot_sink=sink).run(flow))
    assert result["status"] == "error"
    snapshot = result["steps"][0]["snapshot"]
    assert os.path.exists(snapshot["screenshot"])
    assert os.path.exists(snapshot["html"])


def test_remote_snapshot_falls_back_to_local(tmp_path):
    from agent.rpa_core.snapshots import RemoteSnapshotSink

    # Unreachable orchestrator -> upload fails -> falls back to a local snapshot.
    sink = RemoteSnapshotSink("http://127.0.0.1:1", task_id="t", fallback_dir=str(tmp_path))
    result = run_async(sink.save(index=0, step="click(#x)", error="boom", png=b"\x89PNG", html="<html>"))
    assert os.path.exists(result["screenshot"])
    assert os.path.exists(result["html"])


def test_remote_snapshot_falls_back_to_local(tmp_path):
    from agent.rpa_core.snapshots import RemoteSnapshotSink

    # No server at this address -> upload fails -> falls back to local files.
    sink = RemoteSnapshotSink("http://127.0.0.1:1/nope", task_id="t", fallback_dir=str(tmp_path))
    result = run_async(sink.save(index=0, step="click(#x)", error="boom", png=b"PNG", html="<h1>x</h1>"))
    assert os.path.exists(result["screenshot"])
    assert os.path.exists(result["html"])


# -- ExtensionDriver over the browser bridge protocol -----------------------
def _browser_emulator(dom, missing=()):
    """Return (responder, state) emulating the extension/content-script side."""
    state = {"typed": {}, "clicked": []}

    def responder(msg):
        action = msg["action"]
        if action == "goto":
            return {"ok": True, "result": {"url": msg["url"]}}
        if action in ("click", "type"):
            selector = msg.get("selector")
            if selector in missing or selector not in dom:
                return {"ok": False, "code": "locator", "error": "not found"}
            if action == "type":
                state["typed"][selector] = msg["text"]
            else:
                state["clicked"].append(selector)
            return {"ok": True}
        if action == "click_xy":
            state["clicked"].append((msg["x"], msg["y"]))
            return {"ok": True}
        if action == "extract_text":
            value = dom.get(msg["selector"])
            items = value if isinstance(value, list) else ([] if value is None else [value])
            return {"ok": True, "result": items}
        if action == "screenshot":
            return {"ok": True, "result": {"data_url": "data:image/png;base64,Zm9v"}}  # b"foo"
        return {"ok": True, "result": None}

    return responder, state


def test_extension_driver_maps_results_and_locator_error():
    responder, _ = _browser_emulator({"#a": "hello"})
    driver = ExtensionDriver(ScriptedTransport(responder))
    assert run_async(driver.extract_text("#a")) == ["hello"]
    assert run_async(driver.screenshot()) == b"foo"
    with pytest.raises(LocatorError):
        run_async(driver.click("#missing"))


def test_extension_driver_runtime_error_on_non_locator():
    driver = ExtensionDriver(ScriptedTransport(lambda msg: {"ok": False, "error": "boom"}))
    with pytest.raises(RuntimeError):
        run_async(driver.goto("http://x"))


def test_full_flow_through_extension_driver():
    responder, state = _browser_emulator(
        {"#username": "", "#login-btn": "Login", "#orders .order": ["A", "B", "C"]}
    )
    transport = ScriptedTransport(responder)
    flow = load_flow(
        flow={
            "steps": [
                {"action": "goto", "url": "${env.base_url}/login.html"},
                {"action": "type", "selector": "#username", "text": "alice", "heal": False},
                {"action": "click", "selector": "#login-btn", "heal": False},
                {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
            ]
        }
    )
    result = run_async(FlowRunner(ExtensionDriver(transport), env={"base_url": "http://h"}).run(flow))
    assert result["status"] == "success"
    assert result["vars"]["orders"] == ["A", "B", "C"]
    assert state["typed"]["#username"] == "alice"
    assert "#login-btn" in state["clicked"]
    assert transport.sent[0]["action"] == "goto"
    assert transport.sent[0]["url"] == "http://h/login.html"  # ${env.base_url} substituted


def test_self_heal_through_extension_driver():
    # #login-btn missing -> click returns code:locator -> ai_click -> click_xy
    responder, state = _browser_emulator({}, missing={"#login-btn"})
    gateway = FakeAIGateway(locate_map={"登录": (300, 200)})
    flow = load_flow(
        flow={"steps": [{"action": "click", "selector": "#login-btn", "intent": "点击登录按钮"}]}
    )
    result = run_async(
        FlowRunner(ExtensionDriver(ScriptedTransport(responder)), ai_gateway=gateway).run(flow)
    )
    assert result["status"] == "success"
    assert result["steps"][0].get("healed") is True
    assert (300, 200) in state["clicked"]
