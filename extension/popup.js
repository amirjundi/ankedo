const $ = (id) => document.getElementById(id);
const DEFAULTS = { endpoint: "http://127.0.0.1:8000", token: "" };

function show(text, ok) {
  const box = $("msg");
  box.textContent = text;
  box.className = `msg ${ok ? "ok" : "err"}`;
  box.hidden = false;
}

async function load() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  $("endpoint").value = stored.endpoint;
  $("token").value = stored.token;
  // Open straight onto settings the first time, since nothing works without a token.
  if (!stored.token) $("settings").open = true;
}

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    endpoint: $("endpoint").value.trim().replace(/\/+$/, "") || DEFAULTS.endpoint,
    token: $("token").value.trim(),
  });
  show("Saved.", true);
});

$("capture").addEventListener("click", () => {
  const button = $("capture");
  button.disabled = true;
  button.textContent = "Capturing…";

  chrome.runtime.sendMessage({ type: "capture" }, (reply) => {
    button.disabled = false;
    button.textContent = "Capture this post";

    if (!reply?.ok) {
      show(reply?.error || "Something went wrong.", false);
      return;
    }

    const { comments_added, duplicate, found, queued } = reply.data;
    const lines = [];
    lines.push(duplicate ? "Already captured — merged." : "Captured and queued.");
    lines.push(`${comments_added} of ${found} comments added.`);
    if (!duplicate && !queued) lines.push("Not queued for classification.");
    if (found === 0) {
      // Comments load lazily and this extension never clicks. Say so, rather than
      // letting it look like the post had none.
      lines.push("No comments found — expand them on the page, then capture again.");
    }
    show(lines.join("\n"), true);
  });
});

load();
