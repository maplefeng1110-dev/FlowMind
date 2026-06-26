"""PRD §6 acceptance, runnable offline (no browser, no real models) so it can gate CI.

The three PRD acceptance criteria are exercised against the *real* mock-site HTML
(``tests/fixtures/mock_site``) through a BeautifulSoup-backed emulator that speaks
the content-script command protocol. The same DSL flows that run on a real Chrome
(see ``scripts/e2e_chrome_acceptance.py``) run here against the emulator:

  §6.1  engine decoupling   -- run a YAML flow (login -> scrape -> SQLite), no Python
  §6.2  visual self-healing -- rename the login button's id; the flow still completes,
                               first via the cheap DOM-text pick tier (②), then, when
                               that misses, via the visual coordinate tier (③)
  §6.3  local-first routing  -- 100 extractions all hit a local provider (0 cloud)

The real-machine VLM acceptance (a live Qwen-VL + Chrome) stays in the e2e script;
this module locks in the same behavior deterministically.
"""
import asyncio
import json
import sqlite3
import types
from pathlib import Path

import pytest

pytest.importorskip("bs4", reason="beautifulsoup4 is required for the mock-site emulator")
from bs4 import BeautifulSoup  # noqa: E402

from agent.rpa_core.ai_gateway import FakeAIGateway  # noqa: E402
from agent.rpa_core.driver import ExtensionDriver  # noqa: E402
from agent.rpa_core.flow_dsl import load_flow  # noqa: E402
from agent.rpa_core.interpreter import FlowRunner  # noqa: E402
from agent.rpa_core.results_db import SqliteResultWriter  # noqa: E402
from agent.rpa_core.transport import ScriptedTransport  # noqa: E402
from ai.router import AIRouter  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "mock_site"
_REPO_ROOT = Path(__file__).parent.parent


def run_async(coro):
    return asyncio.run(coro)


def _mock_pages(login_html=None):
    """Load the real fixture pages; ``login_html`` overrides login.html (to simulate
    a changed page for self-healing)."""
    return {
        "login.html": login_html
        if login_html is not None
        else (_FIXTURES / "login.html").read_text(encoding="utf-8"),
        "list.html": (_FIXTURES / "list.html").read_text(encoding="utf-8"),
    }


class MockSite:
    """Offline stand-in for the browser bridge, driven by the mock-site HTML. Acts as
    the ``responder`` for :class:`ScriptedTransport`, implementing the same command
    protocol the extension content script does (click/type/extract_text/
    list_interactive/click_xy/screenshot), including onclick-based navigation."""

    INTERACTIVE = "a[href], button, input, select, textarea, [role=button], [onclick]"

    def __init__(self, pages, start="login.html"):
        self._soup = {name: BeautifulSoup(html, "html.parser") for name, html in pages.items()}
        self.current = start
        self.typed = {}
        self.clicked = []
        self._idx_map = {}

    def _soup_now(self):
        return self._soup[self.current]

    def _follow_navigation(self, el):
        onclick = (el.get("onclick") or "") if el is not None else ""
        for name in self._soup:
            if name in onclick:
                self.current = name
                return

    def _select(self, selector):
        if not selector:
            return None
        if selector in self._idx_map:
            return self._idx_map[selector]
        try:
            return self._soup_now().select_one(selector)
        except Exception:  # noqa: BLE001 - invalid selector behaves like "not found"
            return None

    def _list_interactive(self, limit):
        out = []
        self._idx_map = {}
        for el in self._soup_now().select(self.INTERACTIVE):
            if len(out) >= limit:
                break
            idx_selector = '[data-fm-idx="%d"]' % len(out)
            selector = ("#" + el["id"]) if el.get("id") else idx_selector
            self._idx_map[idx_selector] = el
            self._idx_map[selector] = el
            item = {"i": len(out), "tag": el.name, "selector": selector}
            text = el.get_text(strip=True) or (el.get("value") or "")
            if text:
                item["text"] = text
            for attr in ("id", "name", "placeholder", "type", "role"):
                if el.get(attr):
                    item[attr] = el.get(attr)
            out.append(item)
        return out

    def __call__(self, msg):
        action = msg.get("action")
        if action == "goto":
            name = (msg.get("url", "") or "").rsplit("/", 1)[-1]
            if name in self._soup:
                self.current = name
            return {"ok": True, "result": {"url": msg.get("url")}}
        if action in ("click", "type"):
            el = self._select(msg.get("selector"))
            if el is None:
                return {"ok": False, "code": "locator", "error": "not found: %s" % msg.get("selector")}
            if action == "type":
                self.typed[msg.get("selector")] = msg.get("text", "")
            else:
                self.clicked.append(msg.get("selector"))
                self._follow_navigation(el)
            return {"ok": True}
        if action == "type_text":
            return {"ok": True}
        if action == "click_xy":
            self.clicked.append((msg.get("x"), msg.get("y")))
            # A coordinate click follows whatever navigation the page offers (login -> list).
            nav = self._soup_now().select_one("[onclick]")
            self._follow_navigation(nav)
            return {"ok": True}
        if action == "extract_text":
            nodes = self._soup_now().select(msg.get("selector") or "")
            items = [n.get_text(strip=True) for n in nodes if n.get_text(strip=True)]
            return {"ok": True, "result": items[: msg.get("limit", 50)]}
        if action == "list_interactive":
            return {"ok": True, "result": self._list_interactive(msg.get("limit", 120))}
        if action == "page_text":
            return {"ok": True, "result": self._soup_now().get_text()}
        if action == "content":
            return {"ok": True, "result": str(self._soup_now())}
        if action == "screenshot":
            return {"ok": True, "result": {"data_url": "data:image/png;base64,Zm9v"}}
        return {"ok": True, "result": None}


def _driver(site):
    return ExtensionDriver(ScriptedTransport(site))


_ENV = {"base_url": "http://mock", "user": "alice", "password": "pw"}

# §6.1 flow: plain selectors, no AI -- proves the engine runs from YAML alone.
_FLOW_6_1 = {
    "name": "login_and_scrape_plain",
    "steps": [
        {"action": "goto", "url": "${env.base_url}/login.html"},
        {"action": "type", "selector": "#username", "text": "${env.user}", "heal": False},
        {"action": "click", "selector": "#login-btn", "heal": False},
        {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
        {"action": "save_db", "table": "orders", "from": "orders"},
    ],
}


# -- §6.1 engine decoupling -------------------------------------------------
def test_6_1_engine_runs_flow_from_yaml_to_db(tmp_path):
    site = MockSite(_mock_pages())
    db_file = str(tmp_path / "r.db")
    result = run_async(
        FlowRunner(_driver(site), db_writer=SqliteResultWriter(db_file), env=_ENV).run(
            load_flow(flow=_FLOW_6_1)
        )
    )
    assert result["status"] == "success"
    assert site.typed["#username"] == "alice"
    assert "#login-btn" in site.clicked  # navigated login -> list
    assert result["vars"]["orders"] == ["Order A - 100", "Order B - 200", "Order C - 300"]
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT payload FROM orders").fetchall()
    conn.close()
    assert [json.loads(r[0]) for r in rows] == [
        "Order A - 100",
        "Order B - 200",
        "Order C - 300",
    ]


def test_6_1_uses_the_shipped_flow_file(tmp_path):
    # The committed flows/login_and_scrape.yaml parses and runs unchanged (ai_click
    # heals via the gateway), landing rows in SQLite.
    flow_path = _REPO_ROOT / "flows" / "login_and_scrape.yaml"
    site = MockSite(_mock_pages())
    gateway = FakeAIGateway(pick_map={"登录": "#login-btn"})
    db_file = str(tmp_path / "r.db")
    result = run_async(
        FlowRunner(
            _driver(site), ai_gateway=gateway, db_writer=SqliteResultWriter(db_file), env=_ENV
        ).run(load_flow(flow_path=str(flow_path)))
    )
    assert result["status"] == "success"
    conn = sqlite3.connect(db_file)
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert count == 3


# -- §6.2 visual self-healing (selector churn) ------------------------------
def _broken_login_pages():
    # Simulate a redeploy that renamed the login button's id; its visible text and
    # onclick navigation are unchanged, so self-healing can still find it.
    html = (_FIXTURES / "login.html").read_text(encoding="utf-8")
    return _mock_pages(login_html=html.replace('id="login-btn"', 'id="signin-btn"'))


def test_6_2_self_heals_via_dom_pick_tier(tmp_path):
    site = MockSite(_broken_login_pages())
    # Gateway resolves the renamed button by text (tier ②), no screenshot needed.
    gateway = FakeAIGateway(pick_map={"登录": "#signin-btn"})
    db_file = str(tmp_path / "r.db")
    flow_path = _REPO_ROOT / "flows" / "login_and_scrape.yaml"
    result = run_async(
        FlowRunner(
            _driver(site), ai_gateway=gateway, db_writer=SqliteResultWriter(db_file), env=_ENV
        ).run(load_flow(flow_path=str(flow_path)))
    )
    assert result["status"] == "success"
    login_step = next(s for s in result["steps"] if s["action"] == "ai_click")
    assert login_step.get("healed") is True
    assert "#signin-btn" in site.clicked  # DOM-pick clicked the renamed button
    kinds = [c[0] for c in gateway.calls]
    assert "pick" in kinds and "locate" not in kinds  # tier ② only, never reached vision
    conn = sqlite3.connect(db_file)
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert count == 3


def test_6_2_falls_back_to_vision_tier(tmp_path):
    site = MockSite(_broken_login_pages())
    # No DOM-pick match -> escalate to visual coordinates (tier ③).
    gateway = FakeAIGateway(locate_map={"登录": (640, 400)})
    db_file = str(tmp_path / "r.db")
    flow_path = _REPO_ROOT / "flows" / "login_and_scrape.yaml"
    result = run_async(
        FlowRunner(
            _driver(site), ai_gateway=gateway, db_writer=SqliteResultWriter(db_file), env=_ENV
        ).run(load_flow(flow_path=str(flow_path)))
    )
    assert result["status"] == "success"
    assert [c[0] for c in gateway.calls if c[0] in ("pick", "locate")] == ["pick", "locate"]
    assert (640, 400) in site.clicked  # healed via absolute-coordinate click
    conn = sqlite3.connect(db_file)
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    assert count == 3


# -- §6.3 local-first routing (zero cloud cost) -----------------------------
class _FakeClient:
    def __init__(self, content):
        self.content = content
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        message = types.SimpleNamespace(content=self.content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def test_6_3_extractions_stay_local_with_valid_json():
    from ai.jsonutil import loads_lenient

    local = {"id": "ollama", "kind": "local", "client": _FakeClient('{"amount": 100}'),
             "model": "qwen2.5", "vision": True}
    cloud = {"id": "deepseek", "kind": "cloud", "client": _FakeClient("{}"),
             "model": "deepseek-chat", "vision": False}
    router = AIRouter(providers=[cloud, local])  # order must not matter; local wins

    failures = 0
    for _ in range(100):
        out = run_async(
            router.complete("extract", [{"role": "user", "content": "x"}], json_mode=True)
        )
        if not isinstance(loads_lenient(out), dict):
            failures += 1

    assert router.stats == {"local": 100, "cloud": 0}  # never overflowed to cloud
    assert failures == 0  # JSON failure rate 0% (< PRD's 1% budget)
