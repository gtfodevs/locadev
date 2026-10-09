---
name: chrome-debug-profile
description: >
  Signed-in Chrome via remote debugging (CDP): sync a work copy of the Chrome
  profile, launch with --remote-debugging-port, connect Playwright over CDP.
  Use for /chrome-debug-profile, "use my Chrome session", CDP, connectOverCDP,
  logged-in browsing. Pair with /web-requirements for gathering; not for CI e2e.
metadata:
  short-description: "Signed-in Chrome CDP session"
user-invocable: true
---

# /chrome-debug-profile — signed-in session (locadev)

**Means:** the **base session** for the whole AI workflow — not a niche “login helper.”

Proven org pattern: **this skill + Playwright**, then **site skills on top**. Drive Jira, ADO, Slack, Teams, Confluence, chat **PDF/Excel**, etc. with the user’s real SSO. **Prefer this over inventing API keys** for every service.

Does **not** alone write the requirements doc — pair with **`/web-requirements`** or Playwright captures after CDP is up.

Canonical scripts: `~/.grok/skills/chrome-debug-profile/`.

## Safety

- Never point `--user-data-dir` at the **live** Chrome profile while Chrome is open.
- Always use the **work copy** (default `~/.grok/chrome-debug/user-data`).
- Quit Chrome fully before sync. CDP on **127.0.0.1** only.
- Treat as full account access; prefer read-only gather.

## Config

```bash
mkdir -p ~/.grok/local
cp ~/.grok/skills/chrome-debug-profile/config.example.json \
   ~/.grok/local/chrome-debug-profile.json
# edit if non-default Chrome path / profile
```

If missing: **warn** and show setup — do not invent paths.

## Workflow

```bash
# 1) Quit Google Chrome (Cmd+Q)
chrome-profile-sync
# or: ~/.grok/skills/chrome-debug-profile/scripts/sync-chrome-profile.sh

# 2) Debug Chrome (opens start page with append-only URL history)
chrome-debug
# or: ~/.grok/skills/chrome-debug-profile/scripts/launch-chrome-debug.sh

# 3) Confirm
curl -s http://127.0.0.1:9222/json/version
```

**Start page:** history list with **no dedupe**, ordered by **last entered** (newest first).  
Config `startUrl: "start-page"` (default). See `~/.grok/skills/chrome-debug-profile/start-page/`.

Playwright:

```js
import { chromium } from "playwright";
const browser = await chromium.connectOverCDP(
  process.env.CDP_URL || "http://127.0.0.1:9222"
);
const context = browser.contexts()[0];
const page = context.pages()[0] || await context.newPage();
```

## Locadev

After CDP is live → **`/web-requirements`** → citations → **`/grounding`**.  
CI / clean e2e → **`/playwright`** (no this profile).

See `docs/browser-skills.md`.
