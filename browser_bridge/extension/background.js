// FlowMind RPA Bridge - background service worker.
//
// Connects to the Native Messaging Host (which relays to the worker's
// ExtensionDriver), receives {id, action, ...} commands, and routes them to the
// active tab: navigation and screenshots are handled here; DOM operations are
// delegated to the content script. Results are posted back to the host.
//
// The host is persistent and bridges to the worker on its own, so once the native
// port opens it stays open (which also keeps this service worker alive). If the
// host is not registered yet, we reconnect with exponential backoff instead of
// spamming the console.

const HOST_NAME = "com.flowmind.host";
const MIN_BACKOFF = 2000;
const MAX_BACKOFF = 15000;

let port = null;
let backoff = MIN_BACKOFF;

function connect() {
  if (port) return;
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (e) {
    scheduleReconnect("connectNative threw: " + e);
    return;
  }
  port.onMessage.addListener(onCommand);
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    port = null;
    scheduleReconnect(err && err.message ? err.message : "native port closed");
  });
  // The native port is open; the host stays alive and bridges to the worker.
  backoff = MIN_BACKOFF;
  console.log("[flowmind] native host port opened");
}

function scheduleReconnect(reason) {
  console.warn("[flowmind] native host unavailable (" + reason + "); retrying in " + backoff + "ms");
  setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, MAX_BACKOFF);
}

function reply(id, ok, extra) {
  if (port) port.postMessage(Object.assign({ id: id, ok: ok }, extra || {}));
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tabs && tabs[0];
}

async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { action: "ping" });
  } catch (e) {
    // Not injected yet (e.g. tab opened before install) -> inject on demand.
    await chrome.scripting.executeScript({ target: { tabId: tabId }, files: ["content.js"] });
  }
}

function waitForComplete(tabId) {
  return new Promise((resolve) => {
    function listener(updatedTabId, info) {
      if (updatedTabId === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); resolve(); }, 15000);
  });
}

async function onCommand(msg) {
  const id = msg.id;
  try {
    const tab = await getActiveTab();
    if (!tab) { reply(id, false, { error: "no active tab" }); return; }

    if (msg.action === "goto") {
      await chrome.tabs.update(tab.id, { url: msg.url });
      await waitForComplete(tab.id);
      reply(id, true, { result: { url: msg.url } });
      return;
    }

    if (msg.action === "screenshot") {
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
      reply(id, true, { result: { data_url: dataUrl } });
      return;
    }

    // DOM operations -> content script
    await ensureContentScript(tab.id);
    const res = await chrome.tabs.sendMessage(tab.id, msg);
    if (res && res.ok) {
      reply(id, true, { result: res.result });
    } else {
      reply(id, false, { error: (res && res.error) || "command failed", code: res && res.code });
    }
  } catch (e) {
    reply(id, false, { error: String((e && e.message) || e) });
  }
}

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
