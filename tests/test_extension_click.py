"""Runs the real browser_bridge/extension/content.js in Chromium: one `click` command
must activate an element exactly once. Skipped when Playwright/Chromium is missing."""
import asyncio
import os
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.async_api")

CONTENT_JS = (Path(__file__).resolve().parents[1] / "browser_bridge" / "extension" / "content.js").read_text(encoding="utf-8")

PAGE = """<!doctype html><html><body>
<input type="checkbox" id="cb">
<button id="btn" onclick="window.counts.btn++">Add</button>
<form id="f" onsubmit="event.preventDefault(); window.counts.submit++"><button id="submit" type="submit">Go</button></form>
<input type="checkbox" id="labelled"><label id="lbl" for="labelled">Label</label>
<details id="det"><summary id="sum">More</summary>body</details>
<a id="link" href="#target">Jump</a>
<script>
  window.counts = {btn: 0, submit: 0, cb: 0};
  document.getElementById("cb").addEventListener("click", () => window.counts.cb++);
  window.chrome = {runtime: {onMessage: {addListener: (fn) => { window.__listener = fn; }}}};
</script></body></html>"""

SEND = "(sel) => new Promise((res) => window.__listener({action: 'click', selector: sel}, {}, res))"


async def _run():
    async with playwright_api.async_playwright() as p:
        executable = os.getenv("CHROME_BINARY") or None
        try:
            browser = await p.chromium.launch(executable_path=executable)
        except Exception as exc:  # noqa: BLE001 - no usable Chromium in this environment
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            page = await browser.new_page()
            await page.set_content(PAGE)
            await page.add_script_tag(content=CONTENT_JS)
            for selector in ("#cb", "#btn", "#submit", "#lbl", "#sum", "#link"):
                assert (await page.evaluate(SEND, selector)) == {"ok": True}
            return await page.evaluate(
                "({counts: window.counts,"
                " checked: document.getElementById('cb').checked,"
                " labelled: document.getElementById('labelled').checked,"
                " open: document.getElementById('det').open,"
                " hash: location.hash})"
            )
        finally:
            await browser.close()


def test_click_command_activates_each_element_once():
    state = asyncio.run(_run())
    assert state["counts"] == {"btn": 1, "submit": 1, "cb": 1}
    assert state["checked"] is True
    assert state["labelled"] is True
    assert state["open"] is True
    assert state["hash"] == "#target"
