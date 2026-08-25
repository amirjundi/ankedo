// Talks to the local agent. The extractor runs in the page; nothing here touches
// page content beyond forwarding what came back.

const DEFAULTS = { endpoint: "http://127.0.0.1:8000", token: "" };

async function config() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function call(path, options = {}) {
  const { endpoint, token } = await config();
  if (!token) {
    throw new Error("No agent token set. Open the extension options and paste ADMIN_API_TOKEN.");
  }

  let response;
  try {
    response = await fetch(`${endpoint}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });
  } catch (err) {
    // The common case by far: the agent is not running, or is on another port.
    throw new Error(`Cannot reach the agent at ${endpoint}. Is \`ankedo start\` running?`);
  }

  if (response.status === 404) {
    // The router is only mounted when EXTENSION_ENABLED is true, so a 404 here is
    // almost always the feature being off rather than a wrong path.
    throw new Error("The agent has the extension disabled. Set EXTENSION_ENABLED=true and restart it.");
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error("The agent rejected the token. Check ADMIN_API_TOKEN.");
  }
  if (response.status === 503) {
    throw new Error("The agent has no ADMIN_API_TOKEN configured. Run `ankedo setup`.");
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (_) { /* keep the status */ }
    throw new Error(detail);
  }
  return response.json();
}

async function captureActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab.");

  // activeTab means this only works on the tab whose button was pressed, and only
  // after it was pressed. The extension has no standing access to any site.
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["extract.js"],
  }).then(() => chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => ankedoExtract(),
  }));

  if (!result) throw new Error("Could not read the page.");
  if (result.error) throw new Error(result.error);
  if (!result.content_text && result.comments.length === 0) {
    throw new Error("Nothing found on this page — is a post open?");
  }

  const saved = await call("/api/extension/capture", {
    method: "POST",
    body: JSON.stringify(result),
  });

  return {
    ...saved,
    found: result.comments.length,
    platform: result.platform,
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handlers = {
    capture: captureActiveTab,
    status: () => call("/api/extension/status"),
  };
  const handler = handlers[message?.type];
  if (!handler) {
    sendResponse({ ok: false, error: `Unknown message ${message?.type}` });
    return false;
  }

  handler()
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message }));
  return true; // keep the channel open for the async reply
});
