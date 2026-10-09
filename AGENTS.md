# locadev — project rules for Grok / coding agents

This repo is **locadev**: a **full AI workflow** plus **local cloud resources**.

- **You (human)** type and approve.
- **You (agent)** drive work primarily through the **user’s signed-in browser** (`/chrome-debug-profile` + Playwright), gather requirements from **UIs, chat, PDFs, Excel, tickets**, update boards in that session (API keys optional), use **`gh`** for GitHub, implement against desk-hosted Azure/AWS/GCP-shaped services (including managed agent runtimes, profile `agents`), and only claim **ready** after **`/grounding`** passes.

Overview: `README.md`. Spec: `spec.md`. Hooks: `hooks/README.md`. Browser-first: **`docs/browser-skills.md`**. Site: `docs/index.html`.

Skills are authored in **Grok skill format** (`.grok/skills/*/SKILL.md` + optional scripts/local-config). The content is ordinary agent instructions and is straightforward to adapt for Claude Code, Cursor, Codex, or any other LLM host that supports skills or project rules—see README “Agent skills format”.

## Default agent loop (browser-first)

```text
chrome-debug + playwright (signed-in session)
  → gather (web, chat, PDF/Excel, Jira/ADO UI, wikis)
  → clarify in-channel (real UI or local fakes)
  → pre-decision (citations from snapshots)
  → implement + local cloud
  → board/PR evidence (UI or optional API) + gh
  → post-ready → say ready
```

1. **Session** — load **`/chrome-debug-profile`**, ensure CDP (`curl :9222/json/version`). Prefer **work copy** of the user profile so SSO/cookies work. **Do not require API keys** for Jira/ADO/Slack/Teams if the browser session already has them.
2. **Gather** — **`/web-requirements`** (AI plan first) and/or Playwright over CDP. Look beyond structured tickets: **chat threads, PDF/Excel attachments, wiki pages**. Snapshot under `.grok/local/requirements/`.
3. **Site skills** — for recurring orgs/flows, layer thin skills on chrome-debug + playwright (don’t re-solve login).
4. **Clarify** — real Slack/Discord/Teams in the browser, or local fakes for practice.
5. **Pre-decision** — **`/grounding`** / `./hooks/pre-decision.sh` with snapshot paths + quotes. Fail closed if sources conflict.
6. **Local cloud** — `start-docker` if needed; `make up && make verify`; wire from `sandbox.env.example`.
7. **Boards + GitHub** — **default: operate Jira/ADO in the signed-in browser**. Optional: `./boards/board.sh` + tokens when CDP isn’t available (see `boards/README.md`). Assume **`gh`** for PRs. Never invent keys/ids.
8. **Post-ready** — tests/verify + evidence; only then say ready.

## Toolchain assumptions

| Tool | Expectation |
|------|-------------|
| Coding agent | Primary driver of the workflow |
| **`/chrome-debug-profile` + Playwright** | **Primary** access to SaaS (boards, chat, docs) via user session |
| Site skills on top of CDP | Org-specific click paths (Jira project, Teams channel, …) |
| `gh` + GitHub | Issues, PRs, checks when repo work ships |
| `./boards/board.sh` | **Optional** API path for Jira/ADO |
| Slack / Discord / Teams | Real UI via browser, or local compose fakes for practice |
| Docker + compose | Local cloud + fakes |
| Profile `agents` (:8103) | AgentCore / Foundry / Agent Platform runtimes; `/_locadev/events`, `/_locadev/reset`, `/_locadev/scenarios` for evals (see `cloud_agents/README.md`) |

Local channel fakes (practice offline):

| Profile | UI / list |
|---------|-----------|
| `slack` | http://127.0.0.1:8096/ui · `GET /messages` |
| `discord` | http://127.0.0.1:8097/ui · `GET /messages` |
| `teams` | `GET http://127.0.0.1:3979/api/messages` |

## Browser skills (meaning)

Full map: **`docs/browser-skills.md`** (read this — it is the org-proven pattern).

| Skill | Means | Use for |
|-------|--------|---------|
| **`chrome-debug-profile`** | Signed-in Chrome work copy + CDP | **Base session** for almost all external SaaS work |
| **`playwright`** | Drive browser (CDP for workflow; clean Chromium for app e2e) | Automation + **post-ready** UI proof of *your* app |
| **`web-requirements`** | AI plan → browse → requirements doc | **Gather** from any page/thread/attachment you can open |
| Site skills | Thin layer on chrome-debug | Repeatable org flows without API keys |
| **`grounding`** | Citation gates | Pre-decision / post-ready honesty |

Rules:

- **Browser session first**; API tokens second.  
- Requirements may be in **chat + PDF/Excel**, not only Jira fields — open them.  
- Build **site skills on top of** chrome-debug + playwright.  
- Cite snapshot paths; never invent ticket or chat text.  
- Never commit `.grok/local/requirements/**`.

## Docker disk (generic skill + local-config)

Use skill **`external-docker-drive`** (`/external-docker-drive`) for Docker Desktop storage on an external volume.

**Local-config (gitignored):** machine paths live under `.grok/local/`, not in committed skill files.

| File | Git |
|------|-----|
| `.grok/local/external-docker-drive.json` | ignored (active on this machine) |
| `.grok/skills/external-docker-drive/config.example.json` | tracked template |
| `.grok/local/README.md` | tracked (explains the pattern) |

If local config is missing: **warn** and point at the example + setup steps (skill does this). Do not invent paths.

Before `docker` / `make up` after reboot:

```bash
start-docker
```

(`~/bin/start-docker` / `scripts/start-docker.sh` resolve local JSON first.)

## Non-negotiables

- Do not rename the project, compose project, or network away from **`locadev`**.
- Do not publish Service Bus MSSQL as an app database; app SQL is **PGlite** (`:5433`).
- Do not renumber host ports without updating README + `sandbox.env.example` + verify script.
- Prefer documenting emulator limits over silent fidelity gaps.
- Use `docker compose -p locadev` (see Makefile). macOS/Linux bash is the supported path.
- **Do not skip pre/post hooks** when making committing decisions or declaring ready — citations and receipts are required.
- **Do not invent** Jira keys, ADO work item ids, PR numbers, channel agreements, or `gh` output.
- Prefer **browser (CDP)** for Jira/ADO/chat; optional **`./boards/board.sh`** for API. Cite as snapshot paths or `jira:KEY` / `ado:#id`.
- Prefer **browser session over inventing API keys** for SaaS the user already uses.

## Stack smoke workflow

```bash
start-docker
make up && make verify
make test
```

Optional profiles via `./scripts/start.sh` or `docker compose -p locadev --profile <name> up -d`.
