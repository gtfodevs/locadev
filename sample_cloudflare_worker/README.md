# sample_cloudflare_worker

Minimal **Hono + Wrangler** Worker for locadev profile `cloudflare`.

## Ports

| Host | Container | Notes |
|------|-----------|--------|
| **8787** | 8787 | `wrangler dev --local` (workerd) |

## Run via locadev

```bash
# Sample Worker
./scripts/start.sh cloudflare

# Or point at GigChain auth (sibling repo)
CLOUDFLARE_WORKER_DIR=../gigchain/auth ./scripts/start.sh cloudflare
```

Then: `curl http://127.0.0.1:8787/health`

## Standalone

```bash
cd sample_cloudflare_worker
npm install
npm run dev
```

OAuth / JWT secrets for a real auth Worker belong in `.dev.vars` (see that project's `.dev.vars.example`). Compose passes through `ISSUER` and optional GitHub/Google client env vars when set on the host.
