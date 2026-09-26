# locadev

<p align="center">
  <img src="localdev-logo.jpg" alt="locadev logo" width="280" />
</p>

<p align="center">
  <a href="https://github.com/gtfodevs/locadev"><img src="https://img.shields.io/badge/GitHub-gtfodevs%2Flocadev-181717?logo=github&logoColor=white" alt="GitHub gtfodevs/locadev" /></a>
</p>

<p align="center">
  <strong>Repo:</strong> <a href="https://github.com/gtfodevs/locadev">github.com/gtfodevs/locadev</a>
  · <strong>Site:</strong> <code>docs/index.html</code> (Pages or custom subdomain)
  · preview: <code>python3 -m http.server 8088 --directory docs</code>
</p>

<p align="center">
  <sub>Sponsored by</sub><br />
  <a href="https://github.com/gtfodevs" title="GTFO DEVS on GitHub">
    <img src="gtfo-logo.png" alt="GTFO DEVS" height="40" />
  </a>
</p>

**You type. AI runs the rest.** Full AI workflow + local cloud on your desk.

**Proven pattern (multiple orgs):** **`chrome-debug-profile` + Playwright**, then **site skills on top** — drive the **user’s signed-in browser** so Jira, ADO, Slack, Teams, Confluence, chat **PDFs/Excel**, etc. work **without minting API keys** for every service. API CLIs are optional.

**locadev** is the loop where a coding agent does almost everything except the keyboard:

1. **Session** — signed-in Chrome work copy + CDP (`/chrome-debug-profile`)
2. **Gather** — docs, UIs, **chat threads**, **PDF/Excel attachments**, tickets (not only structured Jira fields)
3. **Clarify / update boards** — same browser session (or local channel fakes for practice)
4. **Ship with `gh`** — PRs/checks
5. **Summon local Azure/AWS-shaped resources** in Docker (env-only client wiring)
6. **Pre/post grounding** — citations from **snapshots**, not vibes

You approve; the agent drives. Desk-hosted cloud sidekick means free offline iteration first, real cloud later. Same SDK shapes; swap env when you go live.

Surfaces today: **Azure**, **AWS**, channel fakes, **Cloudflare Workers (local)**, **fake OAuth/TOTP/passkeys**, browser-first boards, optional **boards API CLI**, more via profiles. Hooks keep the AI honest.

| Piece | Where |
|-------|--------|
| Landing page | `docs/index.html` |
| **Browser-first workflow** | [`docs/browser-skills.md`](docs/browser-skills.md) |
| Hooks protocol | [`hooks/README.md`](hooks/README.md) |
| Work boards (browser + optional API) | [`boards/README.md`](boards/README.md) |
| Agent rules | [`AGENTS.md`](AGENTS.md) |

---

## AI workflow (agent-driven, browser-first)

```text
  you type
      │
      ▼
  chrome-debug + playwright (your SSO session)
      │
      ├── gather: web, Jira/ADO UI, chat, PDF/Excel, wikis
      ├── clarify / update boards in the browser (no API keys required)
      └── site skills layered for repeat org flows
      │
      ▼
  pre-decision ◄── snapshot citations
      │
      ▼
  implement + local cloud (make up / verify)
      │
      ▼
  post-ready ◄── tests, verify, board/PR evidence
      │
      ▼
  gh PR / say ready
```

### Work boards (Jira + Azure DevOps)

**Default:** open Jira/ADO in signed-in Chrome via CDP; read/comment/transition in the UI; snapshot for citations.  
**Optional API** when headless or preferred:

```bash
cp boards/config.example.json .grok/local/boards.json   # edit org/project/email
export JIRA_API_TOKEN=…              # only if using API path
export AZURE_DEVOPS_EXT_PAT=…

./boards/board.sh get PROJ-123
./boards/board.sh get 42 --provider ado
```

See **`boards/README.md`** and **`docs/browser-skills.md`**.

### Toolchain assumptions

Warn if missing; do not invent credentials.

| Tool | Role |
|------|------|
| Coding agent (Grok / Claude / Cursor / …) | Drives the loop |
| **`chrome-debug-profile` + Playwright** | **Primary** SaaS access (user session — boards, chat, docs, attachments) |
| **Site skills** on top of CDP | Org-specific flows without new API keys |
| **`gh`** + GitHub auth | Issues, PRs, checks |
| **`./boards/board.sh`** | Optional Jira/ADO API |
| **Slack / Discord / Teams** | Real UI via browser, or local fakes |
| **Docker + this repo** | Local cloud + channel fakes |
| **`web-requirements` / `grounding`** | Gather + citation gates |

Local channel practice (message-visible):

| Profile | See traffic |
|---------|-------------|
| `slack` | http://127.0.0.1:8096/ui · `GET /messages` |
| `discord` | http://127.0.0.1:8097/ui · `GET /messages` |
| `teams` | `GET http://127.0.0.1:3979/api/messages` |

### Pre / post hooks (grounding)

```bash
# Before a committing decision (architecture, ticket rewrite, big implement):
LOCADEV_DECISION='…' LOCADEV_CITATIONS='url; PROJ-1; path' ./hooks/pre-decision.sh

# Before claiming ready / done:
LOCADEV_READY_CLAIM='…' LOCADEV_EVIDENCE='verify:ok; pytest:…; gh:PR #n; jira:…' ./hooks/post-ready.sh
```

Checklists: `hooks/pre-decision.checklist`, `hooks/post-ready.checklist`. Full protocol: **`hooks/README.md`**.

---

## Honest limitations

Say these out loud before you lean on the stack for “production-like” confidence:

| What you might expect | What you actually get |
|---|---|
| Azure Entra / control-plane RBAC | **Topaz** for app-level fine-grained RBAC only — not Entra role assignments or managed identity |
| Full Azure AI Search (semantic rerank, rich OData) | Qdrant-backed emulator: partial `$filter`, approximate hybrid/semantic scores |
| Multi-tenant / multi-connection Postgres | **PGlite** (WASM Postgres + pgvector) for app data — single-connection spirit, not a full server |
| A published SQL Server for apps | The only MSSQL in the stack is **internal to the Service Bus emulator** and is **not** an app DB |
| Real cloud fidelity | Same API shapes and SDK contracts where it matters for local dev; approximations are documented next to the code |

When an emulator only approximates the real service, that approximation is intentional and documented. Prefer finding limits in this README (or service READMEs) over discovering them at runtime.

---

## Prerequisites

- **Docker Desktop** (macOS) or **Docker Engine** (Linux) with `docker compose` v2
- **Python 3.12** for adapter images and host-side tests/demos
- **AI coding agent** for the full workflow (human types / approves)
- **`gh`** (GitHub CLI) installed and authenticated when using the PR/issue path
- **Jira** and/or **Azure DevOps** board access when tickets are in scope (`boards/config.example.json` → `.grok/local/boards.json`; `JIRA_API_TOKEN` / `AZURE_DEVOPS_EXT_PAT`)
- Channel access (**Slack / Discord / Teams**) — or locadev profiles `slack` / `discord` / `teams` for local practice
- Optional: host **[Ollama](https://ollama.com)** or **Claude Code CLI** (`claude auth login`) for real LLM output through the bridge
- Optional GPU: NVIDIA toolkit on Linux for the dockerized Ollama profile; on Mac prefer host Ollama

Primary host path is **macOS / Linux**. Windows/WSL is optional; the supported scripts are bash.

### Docker disk on external drive (local-config + skill)

Images live on an **APFS sparsebundle** on the external drive (not raw ExFAT). Machine paths live in **gitignored local-config** so the skill stays reusable.

```bash
# first time / new machine (local file is gitignored):
mkdir -p .grok/local
cp .grok/skills/external-docker-drive/config.example.json \
   .grok/local/external-docker-drive.json
# edit paths, then after reboot / re-plug:
start-docker
# or: ./scripts/start-docker.sh
```

That script reads local JSON, mounts the sparsebundle → `mountPoint`, sets Docker `dataFolder`, starts Docker Desktop, and waits until the daemon is ready. **Do not open Docker from the menu first** without that volume mounted. If config is missing, `start-docker` **warns** and prints setup steps.

| Piece | Path | Git |
|-------|------|-----|
| Active local config | `.grok/local/external-docker-drive.json` | ignored |
| Local-config docs | `.grok/local/README.md` | tracked |
| Example template | `.grok/skills/external-docker-drive/config.example.json` | tracked |
| Agent skill | `/external-docker-drive` | tracked |
| Project rules | `AGENTS.md` | tracked |

### Agent skills format

Skills under `.grok/skills/` (and user `~/.grok/skills/`) use **Grok’s skill layout**: a directory with `SKILL.md` (YAML frontmatter `name` + `description`, then markdown instructions), optional `scripts/`, `references/`, and `config.example.json`, plus **gitignored** machine config under `.grok/local/`.

That packaging is easy to reuse with other major coding agents that support skills or project instructions—for example:

| Ecosystem | Typical adaptation |
|-----------|-------------------|
| **Claude Code** | Copy into `.claude/skills/` or fold the body into `CLAUDE.md` / project rules |
| **Cursor** | Map to `.cursor/rules` or a Cursor skill package |
| **Codex / other skill hosts** | Same idea: frontmatter description for discovery + markdown steps as the prompt |

The important parts travel unchanged: **when to invoke**, **step-by-step agent behavior**, **local-config paths**, and **scripts**. Only the folder name and discovery config usually need a thin rename for another host.

---

## Quick start

```bash
# 0. Docker on external drive (after reboot / re-plug)
start-docker

# 1. Env for compose substitution
cp .env.example .env

# 2. Start core stack (or pick profiles interactively)
make up
# or: ./scripts/start.sh              # interactive checkboxes
# or: ./scripts/start.sh teams aws    # non-interactive profiles

# 3. Health gate (host-only probes on 127.0.0.1)
make verify
# or: bash scripts/verify.sh

# 4. DaisyUI playground (exercises core services in the browser)
make playground
# → http://127.0.0.1:19191   (see demos/README.md)

# 5. Point a client app at local endpoints
#    Copy only the vars your repo uses from sandbox.env.example
```

Compose project name is **`locadev`**, network **`locadev`**, containers **`locadev-<service>`**.

**Done when:** core health endpoints answer, and a client repo can exercise blob / Service Bus / OpenAI-shaped APIs / app SQL without a cloud subscription and without editing application code.

---

## Port map

Ports are fixed to avoid common local clashes. Do not renumber without a documented reason.

| Service | Host port | Profile | Notes |
|---|---|---|---|
| Azurite blob | **10000** | core | |
| Azurite queue | **10101** | core | host 10001 is often taken |
| Azurite table | **10002** | core | |
| Service Bus AMQP | **5672** | core | real AMQP |
| Service Bus mgmt/health | **5300** | core | |
| mssql (SB backend) | — | core | **not published**; internal only |
| Foundry bridge (Azure OpenAI shape) | **8090** | core | |
| PGlite HTTP (app SQL) | **5433** | core | |
| PGlite PG-wire (optional) | **5432** | core | only if gateway is enabled |
| Redis | **6380** | core | containers use `redis:6379` |
| Topaz REST | **8484** | core | leaves 8383 free for a second Topaz |
| Topaz gRPC | **8485** | core | |
| Cosmos DB vNext | **8081**, **1234** | `cosmos` | HTTP gateway on 8081 |
| MiniStack (AWS gateway) | **4566** | `aws` | S3-focused by default |
| Key Vault (lowkey-vault) | **8443** | `kv` | |
| Qdrant | **6333** | `search` | |
| AI Search emulator | **8800** | `search` | |
| Fake SendGrid | **8095** | `mail` | GET `/captured` to assert mail |
| Fake Slack | **8096** | `slack` | UI `/ui` + GET `/messages` |
| Fake Discord | **8097** | `discord` | UI `/ui` + GET `/messages` |
| Azure Functions | **7071** | `functions` | Runtime + sample; storage → Azurite |
| Cloudflare Worker | **8787** | `cloudflare` | Wrangler `--local` (workerd); override dir via `CLOUDFLARE_WORKER_DIR` |
| Fake OAuth / MFA | **8098** | `oauth` | GitHub + Google OAuth, TOTP 2FA, soft passkeys |
| Fake Twilio (SMS) | **8099** | `sms` | `Messages.json` + GET `/captured` |
| Fake Geocodio | **8100** | `geo` | forward/reverse geocode, deterministic |
| Supabase API (Kong) | **54321** | `supabase` | Supabase CLI stack; Auth/REST/Realtime/Storage |
| Supabase Postgres | **54322** | `supabase` | `postgres:postgres` |
| Supabase Studio | **54323** | `supabase` | |
| Supabase Mailpit | **54324** | `supabase` | auth emails (OTP, magic links) |
| GigChain RPC | **26657** | `gigchain` | CometBFT RPC (Cosmos SDK localnet) |
| GigChain REST | **1317** | `gigchain` | Cosmos REST gateway |
| GigChain gRPC | **9090** | `gigchain` | Cosmos gRPC |
| fake-teams | **3979** | `teams` | GET `/api/messages` |
| echo-bot | **3978** | `teams` | |
| sample_service | **18080** | `sample` | |

**Core (always on):** azurite, mssql, servicebus, bridge, topaz, pglite, redis.

---

## Profiles — pick what to spin up

Optional services are Docker Compose **profiles**. Use the interactive launcher, pass names to `scripts/start.sh`, or:

```bash
docker compose --profile aws --profile search up -d --build
```

| Profile | What it adds | When to enable |
|---|---|---|
| `aws` | MiniStack on **4566** (LocalStack-shaped; S3 by default, `test`/`test`, `us-east-1`) | Any app using `boto3` + `endpoint_url` for S3 (or more AWS APIs as you enable them) |
| `cosmos` | Azure Cosmos DB vNext emulator (**8081**, **1234**) | Document DB / chat-history style clients |
| `search` | Qdrant + Azure AI Search–shaped emulator (**6333**, **8800**) | `azure-search-documents` without a real AI Search resource |
| `kv` | lowkey-vault on **8443** | Key Vault–aware apps (most can stay on `USE_KEY_VAULT=false`) |
| `mail` | Fake SendGrid capture on **8095** | Outbound email without leaving the machine; **see** via `GET /captured` |
| `slack` | Fake Slack Web API on **8096** | Post/history/inject; **see** via `http://127.0.0.1:8096/ui` and `GET /messages` |
| `discord` | Fake Discord REST on **8097** | Create/list/inject; **see** via `http://127.0.0.1:8097/ui` and `GET /messages` |
| `functions` | Functions-style host + sample on **7071** | HTTP + Azurite **queue** I/O (`AzureWebJobsStorage`); real `func start` on host also supported |
| `ollama` | Dockerized Ollama for the bridge | Real local models from the compose network (Mac: often prefer host Ollama) |
| `teams` | fake-teams + echo-bot (no M365 tenant, no tunnel) | Bot Framework / Teams; **see** via `GET /api/messages` |
| `sample` | Minimal in-repo FastAPI consumer on **18080** | Prove end-to-end wiring without another repo |
| `cloudflare` | Wrangler `--local` Worker on **8787** | Cloudflare-shaped APIs (default sample; or `CLOUDFLARE_WORKER_DIR=../gigchain/auth`) |
| `oauth` | Fake GitHub/Google OAuth + TOTP + soft passkeys on **8098** | Local IdP/MFA without real phones or authenticators |
| `sms` | Fake Twilio Messaging on **8099** | Outbound SMS without leaving the machine; **see** via `GET /captured` |
| `geo` | Fake Geocodio on **8100** | Address search / reverse geocode without an API key |
| `supabase` | Local Supabase (Postgres, Auth, PostgREST, Realtime, Storage, Studio, Mailpit) via the **Supabase CLI** on **54321–54324** | Apps built on Supabase; applies the app's own migrations + `seed.sql` |

### Azure Functions + Azurite

Azurite is **core** (always on). Azure Functions needs it for `AzureWebJobsStorage` (triggers/bindings on blob, queue, table).

| How you run Functions | Storage endpoints |
|----------------------|-------------------|
| Profile `functions` (sample container) | Compose DNS: `azurite:10000` / `10001` / `10002` |
| Host `func start` / your app | `127.0.0.1:10000` / **`10101`** (queue) / `10002` — see `sandbox.env.example` |

```bash
./scripts/start.sh functions
curl -s http://127.0.0.1:7071/api/ping
curl -s 'http://127.0.0.1:7071/api/httpHello?name=world'
# queue worker logs:
docker logs locadev-sample-azure-functions 2>&1 | tail -20
```

Details: `sample_azure_functions/README.md`.

### Cloudflare Workers (Wrangler local)

Profile `cloudflare` runs **workerd** via `wrangler dev --local` on host port **8787**.

| How you run it | Build context |
|----------------|---------------|
| `./scripts/start.sh cloudflare` | `./sample_cloudflare_worker` (default) |
| `CLOUDFLARE_WORKER_DIR=../gigchain/auth ./scripts/start.sh cloudflare` | Sibling GigChain auth Worker |

```bash
./scripts/start.sh cloudflare
curl -s http://127.0.0.1:8787/health
docker logs locadev-cloudflare-worker 2>&1 | tail -20
```

OAuth client IDs/secrets: set `GITHUB_*` / `GOOGLE_*` in the environment (or `.env`) before start; the container entrypoint writes them into `.dev.vars` for Wrangler. For **local IdP + MFA**, also enable profile `oauth` and the URL overrides in the section below. Details: `sample_cloudflare_worker/README.md`, `fake_oauth/README.md`.

### Fake identity — OAuth, TOTP 2FA, soft passkeys

Profile `oauth` runs **`fake-oauth`** on host port **8098**: a single local identity service for apps (including GigChain auth) that need IdP + MFA without real GitHub/Google, phones, or platform authenticators.

| Capability | What you get |
|------------|--------------|
| **GitHub-shaped OAuth** | Authorize / token / user / emails (auth-code + PKCE S256) |
| **Google-shaped OAuth** | Authorize / token / userinfo (auth-code + PKCE S256) |
| **TOTP 2FA** | Enroll (secret + otpauth URL), current code, verify |
| **Soft passkeys** | Register → assert → soft-sign → verify (HMAC, not real WebAuthn) |
| **UI + OpenAPI** | http://127.0.0.1:8098/ui · http://127.0.0.1:8098/docs |

```bash
./scripts/start.sh oauth
# or together with Workers auth:
./scripts/start.sh cloudflare oauth

curl -s http://127.0.0.1:8098/health
open http://127.0.0.1:8098/ui   # browser demo for TOTP + soft passkeys
```

#### OAuth endpoints

| Provider | Authorize | Token | Profile / userinfo |
|----------|-----------|-------|--------------------|
| GitHub-shaped | `GET /login/oauth/authorize` | `POST /login/oauth/access_token` | `GET /user`, `GET /user/emails` |
| Google-shaped | `GET /o/oauth2/v2/auth` | `POST /token` | `GET /v1/userinfo` |

- Any `client_id` / `client_secret` accepted.
- Built-in users: **`alice`**, **`bob`** (HTML picker, or `?login=alice`, or `?auto=1` → alice).
- Example authorize (auto-approve alice):

```bash
curl -sI 'http://127.0.0.1:8098/login/oauth/authorize?client_id=local&redirect_uri=http://127.0.0.1:8787/login/oauth/github/callback&response_type=code&auto=1'
```

#### TOTP

```bash
curl -s -X POST http://127.0.0.1:8098/totp/enroll -H 'content-type: application/json'   -d '{"user_id":"alice"}'
curl -s 'http://127.0.0.1:8098/totp/code?user_id=alice'
curl -s -X POST http://127.0.0.1:8098/totp/verify -H 'content-type: application/json'   -d '{"user_id":"alice","code":"<code from above>"}'
```

#### Soft passkeys (API tests)

Not real WebAuthn — deterministic HMAC credentials for integration tests:

```bash
curl -s -X POST http://127.0.0.1:8098/passkey/register -H 'content-type: application/json'   -d '{"user_id":"alice"}'
# then POST /passkey/assert → /passkey/soft-sign → /passkey/verify
```

#### Pair with GigChain auth (Cloudflare local)

1. Start both profiles: `./scripts/start.sh cloudflare oauth`
2. Point auth at the fake via env (see `sandbox.env.example`):

```bash
export CLOUDFLARE_WORKER_DIR=../gigchain/auth
export GITHUB_CLIENT_ID=local-github
export GITHUB_CLIENT_SECRET=local-github-secret
export GITHUB_AUTHORIZE_URL=http://127.0.0.1:8098/login/oauth/authorize
export GITHUB_TOKEN_URL=http://127.0.0.1:8098/login/oauth/access_token
export GITHUB_USERINFO_URL=http://127.0.0.1:8098/user
export GITHUB_EMAILS_URL=http://127.0.0.1:8098/user/emails
# same idea for GOOGLE_* → /o/oauth2/v2/auth, /token, /v1/userinfo
```

From **another compose service**, use host `fake-oauth` (port 8098) instead of `127.0.0.1`.

Full route reference: `fake_oauth/README.md`.

### OpenAI / Groq-compatible bridge routes

Besides the Azure shape, the bridge answers the plain OpenAI and Groq URL shapes with the same backends (`fake` / `ollama` / `claude-cli`). The request's `model` stands in for the Azure deployment name. Tool definitions are accepted and ignored (the fake backend answers with plain text).

| Client | Base URL |
|--------|----------|
| OpenAI SDK / raw `POST /v1/chat/completions`, `/v1/embeddings` | `http://127.0.0.1:8090/v1` |
| Groq (`/openai/v1/chat/completions`) | `http://127.0.0.1:8090/openai/v1` |

### SMS and geocoding fakes (profiles `sms`, `geo`)

- **`sms`** → `fake-twilio` on **8099**: `POST /2010-04-01/Accounts/{sid}/Messages.json`, inspect with `GET /captured`. See `fake_twilio/README.md`.
- **`geo`** → `fake-geocodio` on **8100**: `GET /v1.7/geocode?q=…`, `GET /v1.7/reverse?q=lat,lng`. Deterministic, small built-in gazetteer. See `fake_geocodio/README.md`.

Point the app's provider base URL at the fake (e.g. `TWILIO_API_BASE`, `GEOCODIO_API_BASE`); apps that hard-code the vendor host need a one-line env override.

### Supabase (profile `supabase`)

Supabase is a bundle of services (Postgres, GoTrue auth, PostgREST, Realtime, Storage, Studio, Mailpit). locadev runs it through the **Supabase CLI**, Supabase's own local stack, not as a compose service. That way the consumer's `supabase/config.toml`, migrations, and `seed.sql` apply exactly as they will in the cloud project.

```bash
# dir that CONTAINS supabase/config.toml
export SUPABASE_PROJECT_DIR=../myapp/db
./scripts/start.sh supabase          # or: make supabase / scripts/supabase.sh start
scripts/supabase.sh env              # SUPABASE URL + local demo keys (dotenv form)
scripts/supabase.sh reset            # re-run migrations + seed (local data only)
scripts/supabase.sh stop
```

Without `SUPABASE_PROJECT_DIR`, the minimal `supabase_sample/` project is used. The default `SUPABASE_EXCLUDE` skips `vector,logflare,edge-runtime,imgproxy,supavisor` to keep RAM down. Set it to empty to run everything. Containers land on the same Docker daemon (so on the external DockerData volume) as `supabase_*_<project_id>`, outside the `locadev` compose project and network. From a locadev container, reach them via `host.docker.internal:54321`.

**Limits:** requires the `supabase` CLI on the host. First start pulls ~2–3 GB of images. Phone auth needs `[auth.sms.test_otp]` entries or an SMS provider in the app's `config.toml`.

### Seeing messages on fakes

| Fake | How to inspect traffic in tests / browser |
|------|-------------------------------------------|
| **Slack** (`slack`) | http://127.0.0.1:8096/ui · `GET /messages` · history API |
| **Discord** (`discord`) | http://127.0.0.1:8097/ui · `GET /messages` · channel history REST |
| **Teams** (`teams`) | `GET http://127.0.0.1:3979/api/messages` · transcript endpoints |
| **SendGrid** (`mail`) | `GET http://127.0.0.1:8095/captured` |
| **Twilio** (`sms`) | `GET http://127.0.0.1:8099/captured` |
| **Supabase auth mail** (`supabase`) | http://127.0.0.1:54324 (Mailpit UI) · `GET /api/v1/messages` |

Profiles that are off show as `[--]` in `scripts/verify.sh` rather than failing the core gate. Connectivity tests for optional services **skip** when their port is down.

More clouds and services will be added the same way: new profile, documented ports, consumer env vars, smoke test that skips when off.

---

## Consumer contract

The product surface for client repos is **`sandbox.env.example`**.

1. Copy **only** the variables your app needs.
2. Names may differ per stack (C# `Section:Key` vs Python `UPPER_SNAKE`) — **the values are what matter**.
3. Run the client on the **same host** that publishes Docker ports (`localhost` / `127.0.0.1` from the host; Docker service names from containers).
4. Moving to a real cloud sandbox later is a **connection-string / endpoint swap** — no application code change if you only configured env.

Highlights:

- **Azurite** — full connection string with explicit `BlobEndpoint` / `QueueEndpoint` / `TableEndpoint` on `127.0.0.1:10000/10101/10002` (Python’s `azure-storage-blob` needs this; `UseDevelopmentStorage=true` alone is not enough).
- **Service Bus** — emulator connection string with `UseDevelopmentEmulator=true`; entities are those declared in `infra/Config.json` (`app-work-queue`, `dev-ingestion-queue`, `emailrequest`, `app-events`).
- **Azure OpenAI / Foundry** — `http://127.0.0.1:8090`, any API key (ignored), any deployment name, any `api-version`.
- **App SQL** — PGlite HTTP at `http://127.0.0.1:5433` (optional PG-wire `postgresql://locadev:locadev@127.0.0.1:5432/locadev` if enabled). **Not** the Service Bus internal MSSQL.
- **AWS** — `AWS_ENDPOINT_URL=http://127.0.0.1:4566`, keys `test`/`test`, region `us-east-1` (with profile `aws`).
- **Topaz** — REST authorizer on `http://127.0.0.1:8484`.
- **Redis** — host `127.0.0.1:6380`, containers `redis:6379`.

---

## Bridge backends (Azure OpenAI surface)

The **bridge** presents Azure OpenAI–shaped URLs:

- `POST /openai/deployments/{deployment}/chat/completions`
- `POST /openai/deployments/{deployment}/embeddings`

| `CHAT_BACKEND` / `EMB_BACKEND` | Behavior | Use when |
|---|---|---|
| **`fake`** (default) | Deterministic chat + hash-based embeddings | CI and default local tests |
| **`ollama`** | Real model via `OLLAMA_BASE` (container or host) | Offline structured output |
| **`claude-cli`** | Host `claude -p` (chat only; no embeddings API) | Best quality; **host-only** on Mac/Linux |

Embeddings never route to Claude. Default `EMBED_DIM=1536` must match AI Search / PGlite vector demos. Backend failures return **502** naming the backend.

Prove the client path with:

```bash
python bridge/harness.py   # uses openai.AzureOpenAI against the bridge
```

---

## macOS notes

- Run Docker Desktop and the stack on the **same Mac** that runs tests and client apps.
- Prefer host **Ollama** with `OLLAMA_BASE=http://host.docker.internal:11434` and compose `extra_hosts: ["host.docker.internal:host-gateway"]` on the bridge when the container must reach the host.
- There is **no** WSL requirement and no PowerShell-first path.
- Cosmos (`cosmos` profile) is large and slow under Docker Desktop; raise memory if the emulator fails to stay up — tests skip when port **8081** is down.

---

## Make targets

| Target | Purpose |
|---|---|
| `make start` | Interactive profile launcher |
| `make up` | Core stack `up -d --build` |
| `make teams` | Convenience for teams profile |
| `make down` | Tear down (`ARGS=-v` to drop volumes) |
| `make verify` | Health probes |
| `make test` | Smoke tests |
| `make logs` | Follow compose logs |

---

## Tests and demos

```bash
python3 -m venv .venv
source .venv/bin/activate
pip -q install -r tests/requirements.txt
pytest -q tests
# with teams up and ECHO_BOT_BRAIN empty:
pytest -q tests/teams
```

- **`tests/`** — bare connectivity smokes (one file per service). Optional services skip when not running. Bridge tests use the real `openai.AzureOpenAI` client.
- **`demos/`** — Topaz-gated app patterns (blob, Service Bus, PGlite, Cosmos, Foundry, S3). Policy demo users: `alice@example.com` (editor) allowed on writes; `bob@example.com` (viewer) denied.

Run everything from the Docker **host**, not from inside a random container, so ports match `sandbox.env.example`.

---


### GigChain Cosmos SDK localnet

Profile **`gigchain`** runs GigChain’s Cosmos SDK single-validator node (`gigchaind`) in Docker. This is the **blockchain**, not Azure Cosmos DB (profile `cosmos`).

| Port | Use |
|------|-----|
| **26657** | CometBFT RPC |
| **1317** | REST (gRPC-gateway) |
| **9090** | gRPC |

```bash
GIGCHAIN_CHAIN_DIR=../gigchain/chain ./scripts/start.sh gigchain
curl -s http://127.0.0.1:26657/status | head
```

First image build compiles `gigchaind` (several minutes). By default each container start resets `/data` (`GIGCHAIN_RESET=1`); set `GIGCHAIN_RESET=0` to keep state.

```bash
GIGCHAIN_CHAIN_DIR=../gigchain/chain ./scripts/start.sh cloudflare oauth gigchain
```

See `sample_gigchain/README.md`. On the host without Docker you can also run `make localnet` inside `gigchain/chain`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Service Bus emulator fails to load config | In `infra/Config.json`, `Logging` must be a **sibling of `Namespaces` under `UserConfig`**, not nested inside a namespace |
| Python blob client fails with short connection string | Use the **full** Azurite connection string from `sandbox.env.example` (explicit blob/queue/table endpoints) |
| Service Bus SDK times out on AMQP | Use `azure-servicebus` **≥ 7.13** (pinned **7.14.x** in tests); 7.12.x is too old for the emulator |
| PGlite behaves oddly under heavy pools | Expected: PGlite is a local stand-in, not multi-writer server Postgres; plan a real server for staging concurrency |
| Queue/topic missing on Service Bus | Emulator only creates entities in `infra/Config.json` — edit the file and restart; no runtime create-queue |
| Bridge chat/embeddings 502 | Error body names the backend (`fake` / `ollama` / `claude-cli`); check `CHAT_BACKEND` / `EMB_BACKEND` and Ollama reachability |
| Cosmos never becomes healthy | Give Docker Desktop more RAM/CPU; or leave profile off — tests skip |
| `claude-cli` does nothing in the container | That mode is **host-only**; run the bridge on the host or use `fake` / `ollama` in Docker |
| Port already in use | See the port table — queue is **10101** and Redis is **6380** specifically to dodge common locals |
| MSSQL / Service Bus fail on Apple Silicon | Full `mssql/server:2022` often crashes under QEMU (`Invalid mapping of address`). The compose file uses **azure-sql-edge** as the SB backend (still unpublished, not an app DB). Give Docker enough RAM if edge is slow to start. |
| Service Bus health stays `unhealthy` for ~30–60s | Expected while SQL_WAIT_INTERVAL and entity sync run; wait and re-check `curl http://127.0.0.1:5300/health` |

Health checks only probe ports this stack publishes on `127.0.0.1`. They do not scan networks or firewalls.

---

## What this is not

- Not a real Azure subscription, Entra tenant, or managed identity
- Not Windows-first tooling
- Not a vendor-specific product monorepo
- Not a full multi-writer Postgres by default
- Not a place to embed external sibling application monorepos

---

## Roadmap (non-blocking)

- More cloud providers and services as profiles, same “pick what to spin up” model
- Hardening fake identity (real WebAuthn soft authenticator option, more IdP shapes)
- Optional PG-wire gateway in front of PGlite for `psycopg` / Npgsql without HTTP
- Full `postgres:16` + pgvector profile for heavy concurrency
- Extra AWS services on MiniStack when a consumer flow needs them
- Optional Windows helpers that shell out to the same bash scripts

---

## License / EULAs

Setting `ACCEPT_EULA=Y` in `.env` acknowledges the Microsoft EULAs for **SQL Server for Linux** and the **Service Bus emulator** (dev/test only, no SLA). Other images carry their own licenses (e.g. MiniStack MIT-shaped gateway).
