# AnkEdo Capture — optional Chrome extension

Sends the post you are reading to your local agent, which classifies it through the
normal pipeline. Nothing here is required: the agent collects on its own without it.

It exists for two situations. Playwright has no browser build for some Linux
distributions — Ubuntu 26.04 among them — and this needs none. And a page that only
loads for a signed-in account is already loaded for you, so there is no stored
password, no worker-account warm-up, and no anti-detect launcher involved.

## What it does and does not do

It **reads** the page you have open. It does not click, scroll, or type. Synthetic
input from an extension carries `isTrusted: false`, which these platforms check, and
faking it would need the debugger API — the automation surface this project
deliberately avoids.

The practical consequence: comments that have not loaded are not captured. Expand them
yourself and press capture again — a second capture merges the new comments into the
same post rather than duplicating it.

It uses `activeTab`, not permission on `facebook.com`. It can read a page only after
you press the button, and only that tab.

## Setup

1. Enable it on the agent and restart:

   ```bash
   ankedo configure set EXTENSION_ENABLED=true
   ```

2. Load it: `chrome://extensions` → Developer mode → **Load unpacked** → this
   `extension/` directory.

3. Copy the extension id Chrome shows, and pin it so only this extension may call the
   agent:

   ```bash
   ankedo configure set EXTENSION_ORIGIN=chrome-extension://<the-id>
   ```

4. Restart the agent, open the extension, and paste your `ADMIN_API_TOKEN` from `.env`
   into **Agent settings**.

## Using it

Open a post on Facebook, Instagram or TikTok, expand the comments you care about, and
press **Capture this post**. It appears in the review queue once classified.

If a capture returns no comments, they had not loaded — expand and capture again.

## When selectors drift

Every platform reshuffles its DOM eventually. When that happens this returns *fewer*
comments, never wrong ones: each extractor requires non-empty text before it emits
anything. Fix the selectors in `extract.js`; they are grouped per platform at the top
of the file.
