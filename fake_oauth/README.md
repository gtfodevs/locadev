# fake_oauth (identity fake)

Local **GitHub + Google OAuth**, **TOTP 2FA**, and **soft passkeys** for locadev.

| Host port | Profile |
|-----------|---------|
| **8098** | `oauth` |

- UI: http://127.0.0.1:8098/ui  
- OpenAPI: http://127.0.0.1:8098/docs  

## OAuth

| Provider | Authorize | Token | Userinfo |
|----------|-----------|-------|----------|
| GitHub-shaped | `GET /login/oauth/authorize` | `POST /login/oauth/access_token` | `GET /user`, `GET /user/emails` |
| Google-shaped | `GET /o/oauth2/v2/auth` | `POST /token` | `GET /v1/userinfo` |

PKCE S256 supported. Any `client_id` / `client_secret`. Users: `alice`, `bob` (or `?auto=1`).

## TOTP

```bash
curl -s -X POST http://127.0.0.1:8098/totp/enroll -H 'content-type: application/json' \
  -d '{"user_id":"alice"}'
curl -s 'http://127.0.0.1:8098/totp/code?user_id=alice'
curl -s -X POST http://127.0.0.1:8098/totp/verify -H 'content-type: application/json' \
  -d '{"user_id":"alice","code":"123456"}'
```

## Soft passkeys

HMAC soft credentials (not real WebAuthn). For API/integration tests:

```bash
curl -s -X POST http://127.0.0.1:8098/passkey/register -H 'content-type: application/json' \
  -d '{"user_id":"alice"}'
# then /passkey/assert → /passkey/soft-sign → /passkey/verify
```

## With GigChain auth

```bash
./scripts/start.sh cloudflare oauth
# Point auth URL overrides at http://127.0.0.1:8098/... (or http://fake-oauth:8098 inside compose)
```

See `sandbox.env.example`.
