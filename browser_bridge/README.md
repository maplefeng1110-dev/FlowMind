# FlowMind Browser Bridge (extension + Native Messaging Host)

Commercial-RPA-style browser control: instead of CDP/Playwright, the worker drives
the **active tab** through a browser extension whose **content script** manipulates
the DOM with native JS. Benefits: takes over an already-logged-in session, avoids
CDP/anti-bot detection, and survives browser upgrades.

## Topology

```
ExtensionDriver (worker, Python)
        │  ws://127.0.0.1:8777   (BridgeServerTransport: worker hosts, host connects)
        ▼
Native Messaging Host  (native_host/flowmind_host.py, spawned by the browser)
        │  stdin/stdout (Chrome native messaging framing)
        ▼
Extension background.js  ──chrome.tabs.sendMessage──▶  content.js ──▶ DOM
        └─ goto / screenshot handled in background (tabs API / captureVisibleTab)
```

## Command protocol (JSON, correlated by `id`)

Worker → browser:
```json
{ "id": "<hex>", "action": "click", "selector": "#login-btn", "timeout_ms": 1500 }
```
Browser → worker:
```json
{ "id": "<hex>", "ok": true, "result": ... }
{ "id": "<hex>", "ok": false, "code": "locator", "error": "not found: #x" }
```
`code: "locator"` tells the interpreter to trigger AI visual self-healing.

| action | handled by | result |
|---|---|---|
| `goto {url}` | background | `{url}` |
| `click {selector}` / `type {selector,text}` | content | ok / `code:locator` |
| `type_text {text}` | content (focused element) | ok |
| `scroll {direction,amount}` | content | ok |
| `extract_text {selector,limit}` | content | `[string]` |
| `page_text` / `content` | content | text / outerHTML |
| `screenshot` | background `captureVisibleTab` | `{data_url}` (PNG) |
| `click_xy {x,y}` | content `elementFromPoint` (÷ devicePixelRatio) | ok |

## Install (Chrome / Edge, macOS / Linux)

1. **Load the extension**: `chrome://extensions` → enable Developer mode → *Load unpacked*
   → select `browser_bridge/extension/`. Copy the generated **Extension ID**.
2. **Configure the native host manifest** `native_host/com.flowmind.host.json`:
   - set `path` to the absolute path of `native_host/run_host.sh`;
   - set `allowed_origins` to `chrome-extension://<YOUR_EXTENSION_ID>/`.
3. **Install the manifest** to the browser's NativeMessagingHosts dir:
   - macOS Chrome: `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.flowmind.host.json`
   - Linux Chrome: `~/.config/google-chrome/NativeMessagingHosts/com.flowmind.host.json`
   - Windows: add registry key `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.flowmind.host` → (Default) = manifest path.
   - Edge uses the equivalent `Microsoft Edge` directories.
   ```sh
   chmod +x browser_bridge/native_host/run_host.sh
   cp browser_bridge/native_host/com.flowmind.host.json \
      "$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts/"   # macOS example
   ```
4. **Run the worker** (agent with the `rpa_flow` plugin). It opens the bridge server on
   `127.0.0.1:8777`. Override with `FLOWMIND_BROWSER_BRIDGE_PORT`; the host reads
   `FLOWMIND_BROWSER_BRIDGE_URL` (default `ws://127.0.0.1:8777`).
5. Dispatch `rpa_id=rpa_flow` with a flow. The extension connects the native host on
   first command and drives the active tab.

> Security: `host_permissions: <all_urls>` and `allowed_origins` are broad for setup
> convenience — tighten both to your target sites / extension id for production.

## Automated end-to-end acceptance

`scripts/e2e_chrome_acceptance.py` performs all of the above automatically against a
real Chrome (derives a stable extension id, registers the native host, serves the
mock site, launches Chrome, runs the flow, asserts, then cleans up):

```sh
python scripts/e2e_chrome_acceptance.py                    # PRD 6.1 (no AI needed)
FLOWMIND_AI_GATEWAY_URL=http://127.0.0.1:8000 \
  python scripts/e2e_chrome_acceptance.py                  # + PRD 6.2 visual self-heal
CHROME_BINARY="/path/to/chrome" python scripts/e2e_chrome_acceptance.py
```

Exit codes: `0` pass, `1` assertion failure, `2` environment missing (no Chrome /
openssl / websockets). It launches a visible browser window and temporarily writes
`com.flowmind.host.json` into the browser's NativeMessagingHosts dir (restored on exit).

