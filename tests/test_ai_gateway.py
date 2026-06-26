"""Tests for the shared AI layer: local-first router, vision locate, JSON extract,
and the FastAPI gateway endpoints. All use in-memory fakes (no real models)."""
import asyncio
import base64
import types

import pytest

from ai import extract as extract_mod
from ai import vision as vision_mod
from ai.jsonutil import loads_lenient
from ai.router import AIRouter, build_providers_from_env


def run_async(coro):
    return asyncio.run(coro)


class FakeClient:
    """Minimal stand-in for AsyncOpenAI exposing .chat.completions.create."""

    def __init__(self, content):
        self.content = content
        self.last_kwargs = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.last_kwargs = kwargs
        message = types.SimpleNamespace(content=self.content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeRouter:
    """Stand-in for AIRouter that returns canned completion strings."""

    def __init__(self, responses):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.i = 0
        self.calls = []
        self.stats = {"local": 0, "cloud": 0}

    async def complete(self, task_kind, messages, *, json_mode=False, need_vision=False, temperature=0.0):
        self.calls.append((task_kind, json_mode, need_vision))
        response = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        self.stats["local"] += 1
        return response

    def available(self):
        return ["fake"]


# -- router selection & stats ----------------------------------------------
def test_router_prefers_local_and_passes_json_mode():
    local = {"id": "ollama", "kind": "local", "client": FakeClient('{"x":1}'), "model": "m", "vision": True}
    cloud = {"id": "deepseek", "kind": "cloud", "client": FakeClient("{}"), "model": "d", "vision": True}
    router = AIRouter(providers=[cloud, local])  # order must not matter; local wins
    assert router.select("extract")["id"] == "ollama"
    out = run_async(router.complete("extract", [{"role": "user", "content": "x"}], json_mode=True))
    assert out == '{"x":1}'
    assert router.stats == {"local": 1, "cloud": 0}
    assert local["client"].last_kwargs["response_format"] == {"type": "json_object"}


def test_router_vision_filters_to_capable_provider():
    novision_local = {"id": "l", "kind": "local", "client": FakeClient("{}"), "model": "m", "vision": False}
    vision_cloud = {"id": "c", "kind": "cloud", "client": FakeClient("{}"), "model": "m", "vision": True}
    router = AIRouter(providers=[novision_local, vision_cloud])
    assert router.select("vision", need_vision=True)["id"] == "c"


def test_router_raises_when_no_provider():
    with pytest.raises(RuntimeError):
        AIRouter(providers=[]).select("extract")


def test_build_providers_is_local_first(monkeypatch):
    # build_providers_from_env constructs real AsyncOpenAI clients.
    pytest.importorskip("openai", reason="openai SDK not installed in this environment")
    for var in ["VLLM_BASE_URL", "ARK_API_KEY", "OLLAMA_ENABLED"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    providers = build_providers_from_env()
    assert [p["id"] for p in providers] == ["ollama", "deepseek"]
    assert providers[0]["kind"] == "local"


# -- vision locate ----------------------------------------------------------
def test_locate_parses_coords():
    out = run_async(vision_mod.locate(FakeRouter('{"x": 320.5, "y": 240, "confidence": 0.9}'), b"png", "登录"))
    assert out == {"x": 320.5, "y": 240.0, "confidence": 0.9}


def test_locate_handles_codefence_and_garbage():
    fenced = run_async(vision_mod.locate(FakeRouter('```json\n{"x":1,"y":2}\n```'), b"p", "x"))
    assert fenced["x"] == 1.0 and fenced["y"] == 2.0
    junk = run_async(vision_mod.locate(FakeRouter("sorry, no element found"), b"p", "x"))
    assert junk["x"] is None and junk["confidence"] == 0.0


def test_locate_requests_vision_and_json_mode():
    fake = FakeRouter('{"x":1,"y":1}')
    run_async(vision_mod.locate(fake, b"p", "x"))
    assert fake.calls[0] == ("vision", True, True)


# -- structured extract -----------------------------------------------------
def test_extract_returns_dict():
    out = run_async(extract_mod.extract(FakeRouter('{"a":1}'), text="t", fields={"a": "the a"}))
    assert out == {"a": 1}


def test_extract_retries_until_fields_complete():
    router = FakeRouter(['{"a":1}', '{"a":1,"b":2}'])  # first is missing b
    out = run_async(extract_mod.extract(router, text="t", fields={"a": "", "b": ""}, max_retries=2))
    assert out == {"a": 1, "b": 2}
    assert len(router.calls) == 2


def test_extract_fails_after_retries():
    with pytest.raises(ValueError):
        run_async(extract_mod.extract(FakeRouter("not json"), text="t", max_retries=1))


def test_loads_lenient_variants():
    assert loads_lenient('{"a":1}') == {"a": 1}
    assert loads_lenient('```json\n{"a":1}\n```') == {"a": 1}
    assert loads_lenient('result: {"a":1} done') == {"a": 1}
    assert loads_lenient("nope") is None


# -- DOM-text element pick (tier-2 self-healing) ----------------------------
def test_dompick_returns_index_and_selector():
    from ai import dompick as dompick_mod

    elements = [
        {"i": 0, "tag": "a", "text": "首页", "selector": '[data-fm-idx="0"]'},
        {"i": 1, "tag": "button", "text": "登录", "selector": '[data-fm-idx="1"]'},
    ]
    out = run_async(
        dompick_mod.pick(FakeRouter('{"index":1,"confidence":0.9}'), elements=elements, intent="登录")
    )
    assert out["index"] == 1
    assert out["selector"] == '[data-fm-idx="1"]'


def test_dompick_no_match_returns_negative_index():
    from ai import dompick as dompick_mod

    elements = [{"i": 0, "tag": "a", "text": "首页", "selector": "#home"}]
    out = run_async(dompick_mod.pick(FakeRouter('{"index":-1}'), elements=elements, intent="登录"))
    assert out["index"] == -1
    assert "selector" not in out


def test_dompick_empty_elements_skips_model():
    from ai import dompick as dompick_mod

    router = FakeRouter('{"index":0}')
    out = run_async(dompick_mod.pick(router, elements=[], intent="x"))
    assert out["index"] == -1
    assert router.calls == []  # never bothered the model


def test_dompick_requests_json_mode_text_only():
    from ai import dompick as dompick_mod

    router = FakeRouter('{"index":0,"confidence":1}')
    run_async(dompick_mod.pick(router, elements=[{"i": 0, "selector": "#a", "text": "a"}], intent="a"))
    assert router.calls[0] == ("dompick", True, False)


# -- gateway HTTP endpoints -------------------------------------------------
def _make_app(fake_router):
    from fastapi import FastAPI

    from ai.gateway_api import get_router
    from ai.gateway_api import router as ai_router

    app = FastAPI()
    app.include_router(ai_router)
    app.dependency_overrides[get_router] = lambda: fake_router
    return app


def _client(app):
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TestClient unavailable: {exc}")
    try:
        return TestClient(app)
    except Exception as exc:  # noqa: BLE001 - starlette/httpx version mismatch
        pytest.skip(f"TestClient could not start: {exc}")


def test_locate_endpoint_returns_coords():
    client = _client(_make_app(FakeRouter('{"x":11,"y":22,"confidence":0.8}')))
    img = base64.b64encode(b"screenshot").decode()
    resp = client.post("/ai/locate", json={"image_b64": img, "intent": "登录"})
    assert resp.status_code == 200
    assert resp.json() == {"x": 11.0, "y": 22.0, "confidence": 0.8}


def test_extract_endpoint_ok():
    client = _client(_make_app(FakeRouter('{"a":1,"b":2}')))
    resp = client.post("/ai/extract", json={"text": "...", "fields": {"a": "", "b": ""}})
    assert resp.status_code == 200
    assert resp.json() == {"a": 1, "b": 2}


def test_extract_endpoint_unprocessable_on_bad_output():
    client = _client(_make_app(FakeRouter("not json at all")))
    resp = client.post("/ai/extract", json={"text": "x"})
    assert resp.status_code == 422


def test_pick_endpoint_returns_choice():
    client = _client(_make_app(FakeRouter('{"index":1,"confidence":0.8}')))
    elements = [
        {"i": 0, "selector": "#a", "text": "首页"},
        {"i": 1, "selector": "#b", "text": "登录"},
    ]
    resp = client.post("/ai/pick", json={"elements": elements, "intent": "登录"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["index"] == 1
    assert body["selector"] == "#b"
