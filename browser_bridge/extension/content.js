// FlowMind RPA Bridge - content script.
//
// Runs in the page context and manipulates the DOM with native JS (no CDP), so
// actions look like real user interaction and survive browser upgrades. Receives
// {action, ...} from the background script and returns {ok, result|error, code}.

if (!window.__flowmindContentLoaded) {
  window.__flowmindContentLoaded = true;

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    handle(msg)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true; // keep the message channel open for the async response
  });

  function locatorFail(error) {
    return { ok: false, code: "locator", error: error };
  }

  async function handle(msg) {
    switch (msg.action) {
      case "ping":
        return { ok: true };

      case "click": {
        const el = document.querySelector(msg.selector);
        if (!el) return locatorFail("not found: " + msg.selector);
        el.scrollIntoView({ block: "center", inline: "center" });
        simulateClick(el);
        return { ok: true };
      }

      case "type": {
        const el = document.querySelector(msg.selector);
        if (!el) return locatorFail("not found: " + msg.selector);
        setValue(el, msg.text);
        return { ok: true };
      }

      case "type_text": {
        const el = document.activeElement;
        if (!el || el === document.body) return { ok: false, error: "no focused element" };
        setValue(el, msg.text);
        return { ok: true };
      }

      case "scroll": {
        const delta = (msg.direction === "up" ? -1 : 1) * (msg.amount || 600);
        window.scrollBy(0, delta);
        return { ok: true };
      }

      case "extract_text": {
        const nodes = Array.from(document.querySelectorAll(msg.selector)).slice(0, msg.limit || 50);
        const items = nodes
          .map((n) => (n.innerText || n.textContent || "").trim())
          .filter(Boolean);
        return { ok: true, result: items };
      }

      case "page_text":
        return { ok: true, result: document.body ? document.body.innerText : "" };

      case "content":
        return { ok: true, result: document.documentElement.outerHTML };

      case "click_xy": {
        // Coordinates arrive in captured-image pixels; map back to CSS pixels.
        const dpr = window.devicePixelRatio || 1;
        const cx = msg.x / dpr;
        const cy = msg.y / dpr;
        const el = document.elementFromPoint(cx, cy);
        if (!el) return locatorFail("no element at (" + cx + "," + cy + ")");
        simulateClick(el, cx, cy);
        return { ok: true };
      }

      case "list_interactive":
        return { ok: true, result: listInteractive(msg.limit || 120) };

      default:
        return { ok: false, error: "unknown action: " + msg.action };
    }
  }

  function simulateClick(el, x, y) {
    const opts = { bubbles: true, cancelable: true, view: window };
    if (x != null) {
      opts.clientX = x;
      opts.clientY = y;
    }
    el.dispatchEvent(new MouseEvent("mousedown", opts));
    el.dispatchEvent(new MouseEvent("mouseup", opts));
    el.dispatchEvent(new MouseEvent("click", opts));
    if (typeof el.click === "function" && x == null) el.click();
  }

  function setValue(el, text) {
    el.focus();
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(el, text); // bypass React/Vue value tracking
    } else {
      el.value = text;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // -- DOM-text self-healing (tier 2): serialize visible interactive elements ---
  // The worker sends the list to the AI gateway, which picks the best match for an
  // intent and returns its selector — cheaper and steadier than VLM coordinates.
  const INTERACTIVE_SELECTOR = [
    "a[href]", "button", "input", "select", "textarea", "summary", "label",
    "[role=button]", "[role=link]", "[role=tab]", "[role=menuitem]",
    "[role=checkbox]", "[role=radio]", "[onclick]", "[contenteditable=true]",
  ].join(",");

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    return !el.disabled;
  }

  function describeInteractive(el, idx) {
    const item = { i: idx, tag: el.tagName.toLowerCase(), selector: '[data-fm-idx="' + idx + '"]' };
    const text = (el.innerText || el.textContent || el.value || "").trim().replace(/\s+/g, " ");
    if (text) item.text = text.slice(0, 120);
    ["type", "id", "name", "placeholder", "role"].forEach((attr) => {
      const value = el.getAttribute(attr);
      if (value) item[attr] = value;
    });
    const aria = el.getAttribute("aria-label");
    if (aria) item.aria = aria;
    return item;
  }

  function listInteractive(limit) {
    // Clear stamps left by a previous call so indices stay stable within one heal.
    document.querySelectorAll("[data-fm-idx]").forEach((n) => n.removeAttribute("data-fm-idx"));
    const out = [];
    const all = document.querySelectorAll(INTERACTIVE_SELECTOR);
    for (let i = 0; i < all.length && out.length < limit; i++) {
      const el = all[i];
      if (!isVisible(el)) continue;
      const idx = out.length;
      el.setAttribute("data-fm-idx", String(idx)); // makes [data-fm-idx="N"] actionable
      out.push(describeInteractive(el, idx));
    }
    return out;
  }
}
