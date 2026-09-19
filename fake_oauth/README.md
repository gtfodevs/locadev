# fake_oauth (identity fake)

Local **GitHub + Google OAuth**, **TOTP 2FA**, and **soft passkeys** for locadev — one service, profile `oauth`, port **8098**.

| | |
|--|--|
| Health | http://127.0.0.1:8098/health |
| Browser UI | http://127.0.0.1:8098/ui |
| OpenAPI | http://127.0.0.1:8098/docs |

```bash
./scripts/start.sh oauth
# with GigChain Workers auth:
./scripts/start.sh cloudflare oauth
```

## Capabilities

### OAuth (authorization code + PKCE S256)

| Provider | Authorize | Token | Userinfo |
|----------|-----------|-------|----------|
| GitHub-shaped | `GET /login/oauth/authorize` | `POST /login/oauth/access_token` | `GET /user`, `GET /user/emails` |
| Google-shaped | `GET /o/oauth2/v2/auth` | `POST /token` | `GET /v1/userinfo` |

- Accepts any `client_id` / `client_secret`.
- Users: **`alice`**, **`bob`** — HTML picker, `?login=alice`, or `?auto=1` (alice).
- PKCE: `code_challenge` / `code_verifier` (S256 or plain).

### TOTP 2FA

| Method | Path | Body / query |
|--------|------|----------------|
| POST | `/totp/enroll` | `{"user_id":"alice"}` → secret, otpauth URL, current code |
| GET | `/totp/code` | `?user_id=alice` → current 6-digit code |
| POST | `/totp/verify` | `{"user_id":"alice","code":"123456"}` → `{ok: true/false}` |

RFC 6238 (HMAC-SHA1, 30s, 6 digits). Secrets are in-memory only (reset on container restart).

### Soft passkeys

HMAC soft credentials for **API/integration tests** — not real WebAuthn/platform authenticators.

| Method | Path | Role |
|--------|------|------|
| POST | `/passkey/register` | Create soft credential for `user_id` |
| POST | `/passkey/assert` | Issue challenge + allowCredentials |
| POST | `/passkey/soft-sign` | Simulate authenticator (HMAC signature) |
| POST | `/passkey/verify` | Validate signature against challenge |
| GET | `/passkey/list` | List credentials (`?user_id=` optional) |

Typical flow: register → assert → soft-sign → verify.

## Pair with GigChain auth

```bash
export CLOUDFLARE_WORKER_DIR=../gigchain/auth
export GITHUB_CLIENT_ID=local-github
export GITHUB_CLIENT_SECRET=local-github-secret
export GITHUB_AUTHORIZE_URL=http://127.0.0.1:8098/login/oauth/authorize
export GITHUB_TOKEN_URL=http://127.0.0.1:8098/login/oauth/access_token
export GITHUB_USERINFO_URL=http://127.0.0.1:8098/user
export GITHUB_EMAILS_URL=http://127.0.0.1:8098/user/emails
export GOOGLE_CLIENT_ID=local-google
export GOOGLE_CLIENT_SECRET=local-google-secret
export GOOGLE_AUTHORIZE_URL=http://127.0.0.1:8098/o/oauth2/v2/auth
export GOOGLE_TOKEN_URL=http://127.0.0.1:8098/token
export GOOGLE_USERINFO_URL=http://127.0.0.1:8098/v1/userinfo
./scripts/start.sh cloudflare oauth
```

Inside the compose network, replace `127.0.0.1` with hostname **`fake-oauth`**. Full env template: `sandbox.env.example`.
