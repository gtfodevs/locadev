import { Hono } from 'hono'

type Env = { Bindings: { ISSUER?: string } }

const app = new Hono<Env>()

app.get('/health', (c) =>
  c.json({
    ok: true,
    service: 'locadev-sample-cloudflare-worker',
    issuer: c.env.ISSUER ?? 'http://127.0.0.1:8787',
  }),
)

app.get('/', (c) =>
  c.json({
    message: 'locadev Cloudflare Workers sample (wrangler --local)',
    health: '/health',
  }),
)

export default app
