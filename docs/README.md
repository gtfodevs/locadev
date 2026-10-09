# Project website (`docs/`)

Static landing page for **locadev**.

**Source of truth (always link here):** [github.com/gtfodevs/locadev](https://github.com/gtfodevs/locadev)

The site may be served from **GitHub Pages** or a **custom subdomain**. The page uses **absolute GitHub URLs** (clone, README, issues, boards/hooks docs) so a pretty domain never looks like a disconnected product without a repo.

**Positioning:** *You type. AI runs the rest.* — **browser-first** (chrome-debug + Playwright + site skills; no API keys required for boards/chat) → gather from UIs/chat/PDF/Excel → grounding → local cloud → `gh`.

| Doc | Topic |
|-----|--------|
| [browser-skills.md](./browser-skills.md) | **Proven pattern**: session + Playwright + layered skills |
| [../boards/README.md](../boards/README.md) | Jira + Azure DevOps |
| [../hooks/README.md](../hooks/README.md) | Pre/post grounding |
| [../cloud_agents/README.md](../cloud_agents/README.md) | Managed agent runtimes (AgentCore, Foundry, Agent Platform), scenarios, eval hooks |
| [../bridge/README.md](../bridge/README.md) | Model bridge: Azure OpenAI, OpenAI, Responses, Groq, Bedrock Converse, Gemini |

No build step — pure HTML/CSS + logos + **inline GitHub mark SVG** (no CDN).

| Asset | Role |
|-------|------|
| `localdev-logo.jpg` | LocaDev wordmark / favicon |
| `gtfo-logo.png` | **Sponsored by** mark → links to [github.com/gtfodevs](https://github.com/gtfodevs) (org home, not the locadev repo) |

## Preview locally (this works even before GitHub Pages)

From the **repo root**:

```bash
python3 -m http.server 8088 --directory docs
```

Then open: **http://127.0.0.1:8088/**

Or open the file directly:

```bash
open docs/index.html
```

If the browser says **failed to load page**:

| Cause | Fix |
|--------|-----|
| Nothing listening on the port | Run the `python3 -m http.server` command above |
| Wrong folder | Server must use `--directory docs` (not the repo root alone) |
| Opened a GitHub **blob** URL | HTML on `github.com/.../blob/...` is **not** a website — use Pages or local preview |
| GitHub Pages 404 | Enable Pages (below) and wait a few minutes after push |
| Placeholder `YOUR_ORG` 404s | Only needed if you still link to a fake GitHub org; page itself no longer depends on that |

## Enable GitHub Pages

1. Push `docs/` to your default branch  
2. Repo **Settings → Pages**  
3. **Source:** Deploy from a branch  
4. **Branch:** `main` (or default), folder: **`/docs`**  
5. Save  

Default Pages URL:

```text
https://gtfodevs.github.io/locadev/
```

### Custom subdomain

In the repo **Settings → Pages → Custom domain**, set your subdomain (e.g. `locadev.example.com`) and add the DNS CNAME GitHub shows. Optional: `docs/CNAME` file with that host.

The landing page already labels itself as the **product page** and points clone/issues/docs at **github.com/gtfodevs/locadev** so visitors aren’t stranded on a logo-only domain.

If the GitHub org/user ever renames, update absolute links in `docs/index.html` (search `gtfodevs/locadev`).

## Files

| File | Role |
|------|------|
| `index.html` | Landing page |
| `localdev-logo.jpg` | Logo (same as repo root) |

Colors match the logo wordmark: **Loca** orange `#ff9f1a`, **Dev** pink `#ff2d95`.
