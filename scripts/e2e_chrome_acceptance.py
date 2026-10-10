#!/usr/bin/env python3
"""Real-Chrome end-to-end acceptance for the FlowMind browser bridge.

Exercises the FULL chain with no mocks:

    worker (ExtensionDriver + BridgeServerTransport)
        <-- WebSocket -->  Native Messaging Host (persistent, self-reconnecting)
        <-- stdin/stdout --> Chrome extension (background + content script)
        <-- DOM -->

Design notes that make this reliable:
  * The native host is registered PERMANENTLY (not cleaned up) and self-reconnects to
    the worker, so the extension<->host link stays up across runs.
  * Whatever FlowMind extension is loaded in the persistent profile is auto-authorized
    (we scan the profile and rewrite allowed_origins), so you never hand-edit ids.
  * A persistent profile under browser_bridge/.e2e/profile remembers the extension, so
    the one-time manual "Load unpacked" (required only on Google Chrome) is truly once.

Extension loading by browser:
  * Chromium / Chrome for Testing / Canary -> --load-extension works, fully automatic.
  * Google Chrome (stable) -> CLI side-load is blocked; FIRST run load it once
    (chrome://extensions -> Developer mode -> Load unpacked -> the printed path).

Phases: PRD 6.1 (always); PRD 6.2 visual self-heal if FLOWMIND_AI_GATEWAY_URL is set.

Usage:
    python scripts/e2e_chrome_acceptance.py
    CHROME_BINARY="/path/to/chrome-for-testing" python scripts/e2e_chrome_acceptance.py
    FLOWMIND_AI_GATEWAY_URL=http://127.0.0.1:8000 python scripts/e2e_chrome_acceptance.py

Exit codes: 0 pass, 1 assertion/connection failure, 2 environment missing.
"""
import asyncio
import base64
import functools
import hashlib
import http.server
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EXT_SRC = os.path.join(ROOT, "browser_bridge", "extension")
HOST_SCRIPT = os.path.join(ROOT, "browser_bridge", "native_host", "flowmind_host.py")
MOCK_SITE = os.path.join(ROOT, "browser_bridge", "mock_site")
STABLE = os.path.join(ROOT, "browser_bridge", ".e2e")
EXT_DIR = os.path.join(STABLE, "extension")
PROFILE_DIR = os.path.join(STABLE, "profile")
BRIDGE_PORT = int(os.getenv("FLOWMIND_BROWSER_BRIDGE_PORT", "8777"))
CONNECT_WAIT = int(os.getenv("FLOWMIND_E2E_CONNECT_WAIT", "150"))

LOGIN_MOVED_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Mock Login Moved</title></head>
<body>
  <h1>Login</h1>
  <form id="login-form">
    <input id="username" name="username" placeholder="username">
    <input id="password" name="password" type="password" placeholder="password">
    <button id="signin-x" style="position:absolute; left:420px; top:280px;" type="button"
            onclick="window.location.href='list.html'">登 录</button>
  </form>
</body></html>
"""


def log(msg):
    print("[e2e] " + msg, flush=True)


def fail(msg):
    print("[e2e] FAIL: " + msg, file=sys.stderr, flush=True)
    raise SystemExit(1)


def skip(msg):
    print("[e2e] SKIP: " + msg, file=sys.stderr, flush=True)
    raise SystemExit(2)


def find_chrome():
    explicit = os.getenv("CHROME_BINARY")
    if explicit and os.path.exists(explicit):
        return explicit
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def native_messaging_dir():
    override = os.getenv("FLOWMIND_NM_DIR")
    if override:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome/NativeMessagingHosts")
    return os.path.expanduser("~/.config/google-chrome/NativeMessagingHosts")


def ext_id_from_der(der_bytes):
    digest = hashlib.sha256(der_bytes).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


def make_or_load_extension():
    """Create (or reuse) a key-pinned copy of the extension; return (dir, id)."""
    os.makedirs(STABLE, exist_ok=True)
    manifest_path = os.path.join(EXT_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path))
        key = manifest.get("key")
        if key:
            return EXT_DIR, ext_id_from_der(base64.b64decode(key))

    if os.path.isdir(EXT_DIR):
        shutil.rmtree(EXT_DIR)
    shutil.copytree(EXT_SRC, EXT_DIR)
    priv = os.path.join(STABLE, "key.pem")
    der = os.path.join(STABLE, "pub.der")
    subprocess.run(["openssl", "genrsa", "-out", priv, "2048"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "rsa", "-in", priv, "-pubout", "-outform", "DER", "-out", der],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    der_bytes = open(der, "rb").read()
    manifest = json.load(open(manifest_path))
    manifest["key"] = base64.b64encode(der_bytes).decode()
    json.dump(manifest, open(manifest_path, "w"), indent=2)
    return EXT_DIR, ext_id_from_der(der_bytes)


def write_launcher():
    launcher = os.path.join(STABLE, "run_host_e2e.sh")
    with open(launcher, "w") as handle:
        handle.write("#!/usr/bin/env bash\n")
        handle.write('exec "%s" "%s"\n' % (sys.executable, HOST_SCRIPT))
    os.chmod(launcher, 0o755)
    return launcher


def loaded_flowmind_ids():
    """Scan the persistent profile for any loaded FlowMind bridge extension ids."""
    ids = set()
    for name in ("Default/Preferences", "Default/Secure Preferences"):
        path = os.path.join(PROFILE_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path))
        except Exception:  # noqa: BLE001
            continue
        settings = (data.get("extensions") or {}).get("settings") or {}
        for ext_id, info in settings.items():
            if "browser_bridge" in str(info.get("path") or ""):
                ids.add(ext_id)
    return ids


def write_host_manifest(launcher, ids):
    """Register the native host PERMANENTLY, authorizing the given extension ids."""
    nm_dir = native_messaging_dir()
    os.makedirs(nm_dir, exist_ok=True)
    target = os.path.join(nm_dir, "com.flowmind.host.json")
    json.dump(
        {
            "name": "com.flowmind.host",
            "description": "FlowMind RPA native messaging host",
            "path": launcher,
            "type": "stdio",
            "allowed_origins": ["chrome-extension://%s/" % i for i in sorted(ids)],
        },
        open(target, "w"),
        indent=2,
    )
    return target


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_http_server(root):
    handler = functools.partial(_QuietHandler, directory=root)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def kill_stale_chrome():
    """Kill any prior Chrome bound to OUR e2e profile (never the user's main Chrome),
    so a fresh launch reloads the latest extension code instead of attaching to a
    stale singleton instance."""
    try:
        subprocess.run(["pkill", "-f", "user-data-dir=" + PROFILE_DIR],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
    except Exception:  # noqa: BLE001
        pass


def launch_chrome(chrome, url, ext_dir):
    env = dict(os.environ)
    env["FLOWMIND_BROWSER_BRIDGE_URL"] = "ws://127.0.0.1:%d" % BRIDGE_PORT
    args = [
        chrome,
        "--user-data-dir=" + PROFILE_DIR,
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
        "--load-extension=" + ext_dir,
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    return subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def wait_for_bridge(transport, ext_dir, launcher, base_ids):
    """Wait for the host to connect, re-authorizing any loaded extension as it appears."""
    start = time.time()
    written = set(base_ids)
    instructed = False
    while time.time() - start < CONNECT_WAIT:
        if transport._connected.is_set():
            return True
        ids = set(base_ids) | loaded_flowmind_ids()
        if ids != written:
            write_host_manifest(launcher, ids)
            written = ids
            log("authorized loaded extension id(s): %s" % ", ".join(sorted(ids)))
        if not instructed and time.time() - start > 8:
            print("\n[e2e] -------------------------------------------------------------")
            print("[e2e] If nothing connects, load the extension ONCE in the opened window")
            print("[e2e] (Google Chrome blocks CLI side-load; the profile then remembers it):")
            print("[e2e]   chrome://extensions -> Developer mode -> Load unpacked -> (Cmd+Shift+G)")
            print("[e2e]   %s" % ext_dir)
            print("[e2e] -------------------------------------------------------------\n")
            instructed = True
        await asyncio.sleep(0.5)
    return transport._connected.is_set()


async def run_flow(driver, flow, db=None, gateway_url=None):
    from agent.rpa_core.ai_gateway import AIGatewayClient
    from agent.rpa_core.flow_dsl import load_flow
    from agent.rpa_core.interpreter import FlowRunner

    ai = AIGatewayClient(gateway_url) if gateway_url else None
    runner = FlowRunner(driver, ai_gateway=ai, db_writer=db, env={})
    return await runner.run(load_flow(flow=flow))


async def acceptance_6_1(driver, base_url, db, db_path):
    log("PRD 6.1: login -> scrape -> save_db (YAML-only, no AI) ...")
    flow = {
        "name": "e2e_login_scrape",
        "steps": [
            {"action": "goto", "url": base_url + "/login.html", "timeout_ms": 20000},
            {"action": "type", "selector": "#username", "text": "alice", "heal": False},
            {"action": "type", "selector": "#password", "text": "secret", "heal": False},
            {"action": "click", "selector": "#login-btn", "heal": False},
            {"action": "wait", "amount": 1500},
            {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
            {"action": "save_db", "table": "orders", "from": "orders"},
        ],
    }
    result = await run_flow(driver, flow, db=db)
    if result.get("status") != "success":
        fail("flow status=%s detail=%s" % (result.get("status"), result.get("steps")))
    orders = result.get("vars", {}).get("orders")
    expected = ["Order A - 100", "Order B - 200", "Order C - 300"]
    if orders != expected:
        fail("orders mismatch: got %r" % (orders,))
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT payload FROM orders").fetchall()
    conn.close()
    if len(rows) != 3:
        fail("expected 3 DB rows, got %d" % len(rows))
    log("PRD 6.1 PASSED: scraped %d orders and persisted to SQLite" % len(rows))


async def acceptance_6_2(driver, base_url, web_root, gateway_url):
    log("PRD 6.2: visual self-heal via ai_click (renamed/moved button) ...")
    open(os.path.join(web_root, "login_moved.html"), "w").write(LOGIN_MOVED_HTML)
    flow = {
        "name": "e2e_self_heal",
        "steps": [
            {"action": "goto", "url": base_url + "/login_moved.html", "timeout_ms": 20000},
            {"action": "type", "selector": "#username", "text": "alice", "heal": False},
            {"action": "ai_click", "selector": "#login-btn", "intent": "页面上的登录按钮"},
            {"action": "wait", "amount": 1500},
            {"action": "extract_text", "selector": "#orders .order", "save_as": "orders"},
        ],
    }
    result = await run_flow(driver, flow, gateway_url=gateway_url)
    if result.get("status") != "success":
        fail("self-heal flow status=%s detail=%s" % (result.get("status"), result.get("steps")))
    if not result.get("vars", {}).get("orders"):
        fail("self-heal did not reach the orders page")
    log("PRD 6.2 PASSED: AI located the moved button and the flow continued")


async def main_async():
    chrome = find_chrome()
    if not chrome:
        skip("no Chrome/Chromium found (set CHROME_BINARY=/path/to/chrome)")
    if not shutil.which("openssl"):
        skip("openssl not found (needed to derive a stable extension id)")
    try:
        import websockets  # noqa: F401
    except Exception:  # noqa: BLE001
        skip("python 'websockets' package not installed")

    from agent.rpa_core.driver import ExtensionDriver
    from agent.rpa_core.results_db import SqliteResultWriter
    from agent.rpa_core.transport import BridgeServerTransport

    os.environ["FLOWMIND_BROWSER_BRIDGE_PORT"] = str(BRIDGE_PORT)
    gateway_url = os.getenv("FLOWMIND_AI_GATEWAY_URL")

    ext_dir, ext_id = make_or_load_extension()
    launcher = write_launcher()
    base_ids = {ext_id} | loaded_flowmind_ids()
    host_target = write_host_manifest(launcher, base_ids)
    log("extension id: %s" % ext_id)
    log("native host registered (permanent): %s" % host_target)

    rundir = tempfile.mkdtemp(prefix="flowmind-e2e-")
    web_root = os.path.join(rundir, "site")
    shutil.copytree(MOCK_SITE, web_root)
    db_path = os.path.join(rundir, "orders.db")

    httpd = chrome_proc = None
    try:
        httpd, port = start_http_server(web_root)
        base_url = "http://127.0.0.1:%d" % port
        log("serving mock site at %s" % base_url)

        transport = BridgeServerTransport(port=BRIDGE_PORT, connect_timeout=CONNECT_WAIT)
        await transport.start()
        os.makedirs(PROFILE_DIR, exist_ok=True)
        kill_stale_chrome()
        log("launching Chrome (headed) ...")
        chrome_proc = launch_chrome(chrome, base_url + "/login.html", ext_dir)

        log("waiting for the browser bridge to connect (up to %ds) ..." % CONNECT_WAIT)
        if not await wait_for_bridge(transport, ext_dir, launcher, base_ids):
            fail("browser bridge never connected (extension not loaded). See the steps above.")
        log("browser bridge connected")

        driver = ExtensionDriver(transport)
        db = SqliteResultWriter(db_path)
        await acceptance_6_1(driver, base_url, db, db_path)
        if gateway_url:
            await acceptance_6_2(driver, base_url, web_root, gateway_url)
        else:
            log("PRD 6.2 skipped (set FLOWMIND_AI_GATEWAY_URL to run visual self-heal)")

        await transport.close()
        log("ALL ACCEPTANCE CHECKS PASSED")
    finally:
        if chrome_proc is not None:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                chrome_proc.kill()
        if httpd is not None:
            httpd.shutdown()
        shutil.rmtree(rundir, ignore_errors=True)
        log("done. Native host stays registered at: %s" % host_target)
        log("   (to remove later: rm '%s')" % host_target)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
