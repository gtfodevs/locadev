"""locadev fake identity: GitHub/Google OAuth + TOTP 2FA + soft passkeys.

OAuth: authorization-code + PKCE (S256), any client_id/secret.
TOTP: RFC 6238 via stdlib HMAC-SHA1.
Passkeys: soft HMAC credentials for API tests (not real WebAuthn).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="locadev-fake-oauth", version="0.2.0")

_codes: dict[str, dict[str, Any]] = {}
_tokens: dict[str, dict[str, Any]] = {}
_totp: dict[str, dict[str, Any]] = {}
_passkeys: dict[str, dict[str, Any]] = {}
_challenges: dict[str, dict[str, Any]] = {}

_USERS: dict[str, dict[str, Any]] = {
    "alice": {
        "github": {
            "id": 1001,
            "login": "alice",
            "name": "Alice Example",
            "email": "alice@example.com",
            "emails": [{"email": "alice@example.com", "primary": True, "verified": True}],
        },
        "google": {
            "sub": "google-alice",
            "email": "alice@example.com",
            "email_verified": True,
            "name": "Alice Example",
        },
    },
    "bob": {
        "github": {
            "id": 1002,
            "login": "bob",
            "name": "Bob Example",
            "email": "bob@example.com",
            "emails": [{"email": "bob@example.com", "primary": True, "verified": True}],
        },
        "google": {
            "sub": "google-bob",
            "email": "bob@example.com",
            "email_verified": True,
            "name": "Bob Example",
        },
    },
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _purge(store: dict[str, dict[str, Any]], ttl: float = 600.0) -> None:
    now = time.time()
    for k in [k for k, v in store.items() if now - float(v.get("created_at", now)) > ttl]:
        store.pop(k, None)


def _totp_at(secret_b32: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    counter = int((for_time if for_time is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def _totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    now = int(time.time())
    want = code.strip()
    for w in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret_b32, now + w * 30), want):
            return True
    return False


def _pkce_ok(verifier: str, challenge: str | None, method: str | None) -> bool:
    if not challenge:
        return True
    method = (method or "S256").upper()
    if method == "PLAIN":
        return hmac.compare_digest(verifier, challenge)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return hmac.compare_digest(_b64url(digest), challenge)


def _picker_html(provider: str, qs: dict[str, str]) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{v}" />' for k, v in qs.items() if k != "login"
    )
    buttons = "".join(f'<button type="submit" name="login" value="{u}">{u}</button> ' for u in _USERS)
    return f"""<!doctype html>
<html><head><title>locadev fake {provider}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto}}
button{{margin:.25rem;padding:.5rem 1rem}}</style></head>
<body>
<h1>Fake {provider} sign-in</h1>
<p>locadev identity fake — pick a user. <a href="/ui">MFA UI</a></p>
<form method="get" action="">{hidden}{buttons}</form>
</body></html>"""


def _authorize(
    *,
    provider: str,
    client_id: str | None,
    redirect_uri: str | None,
    state: str | None,
    scope: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
    login: str | None,
    auto: str | None,
) -> Any:
    _purge(_codes)
    if not redirect_uri or not client_id:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "client_id and redirect_uri required"},
            status_code=400,
        )
    user_key = (login or "").strip().lower() or None
    if auto in ("1", "true", "yes") and not user_key:
        user_key = "alice"
    if not user_key or user_key not in _USERS:
        qs = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code"}
        if state:
            qs["state"] = state
        if scope:
            qs["scope"] = scope
        if code_challenge:
            qs["code_challenge"] = code_challenge
        if code_challenge_method:
            qs["code_challenge_method"] = code_challenge_method
        if auto:
            qs["auto"] = auto
        return HTMLResponse(_picker_html(provider, qs))

    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "provider": provider,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "profile": _USERS[user_key][provider],
        "login": user_key,
        "created_at": time.time(),
    }
    params: dict[str, str] = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


async def _token_exchange(request: Request) -> dict[str, Any]:
    _purge(_codes)
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        data = await request.json()
    else:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}

    code = data.get("code")
    redirect_uri = data.get("redirect_uri")
    verifier = data.get("code_verifier") or ""
    if not code or code not in _codes:
        return {"error": "invalid_grant", "error_description": "unknown code"}
    rec = _codes.pop(code)
    if redirect_uri and redirect_uri != rec["redirect_uri"]:
        return {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}
    if not _pkce_ok(verifier, rec.get("code_challenge"), rec.get("code_challenge_method")):
        return {"error": "invalid_grant", "error_description": "pkce failed"}

    token = secrets.token_urlsafe(32)
    _tokens[token] = {
        "provider": rec["provider"],
        "profile": rec["profile"],
        "client_id": rec["client_id"],
        "created_at": time.time(),
    }
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": rec.get("scope") or "",
    }


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _soft_sign(credential_id: str, challenge: str) -> str:
    rec = _passkeys[credential_id]
    return _b64url(hmac.new(rec["secret"], challenge.encode("utf-8"), hashlib.sha256).digest())


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "fake-oauth",
        "features": ["oauth", "totp", "passkey"],
        "providers": ["github", "google"],
        "pending_codes": len(_codes),
        "tokens": len(_tokens),
        "totp_users": list(_totp.keys()),
        "passkeys": len(_passkeys),
        "users": list(_USERS.keys()),
    }


# GitHub-shaped
@app.get("/login/oauth/authorize")
def github_authorize(
    client_id: str | None = None,
    redirect_uri: str | None = None,
    state: str | None = None,
    scope: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    login: str | None = None,
    auto: str | None = None,
) -> Any:
    return _authorize(
        provider="github",
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        login=login,
        auto=auto,
    )


@app.post("/login/oauth/access_token")
async def github_token(request: Request) -> JSONResponse:
    return JSONResponse(await _token_exchange(request))


@app.get("/user")
def github_user(request: Request) -> Any:
    token = _bearer(request)
    if not token or token not in _tokens or _tokens[token]["provider"] != "github":
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    p = _tokens[token]["profile"]
    return {"id": p["id"], "login": p["login"], "name": p["name"], "email": p["email"]}


@app.get("/user/emails")
def github_emails(request: Request) -> Any:
    token = _bearer(request)
    if not token or token not in _tokens or _tokens[token]["provider"] != "github":
        return JSONResponse({"message": "Bad credentials"}, status_code=401)
    return _tokens[token]["profile"]["emails"]


# Google-shaped
@app.get("/o/oauth2/v2/auth")
def google_authorize(
    client_id: str | None = None,
    redirect_uri: str | None = None,
    state: str | None = None,
    scope: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    login: str | None = None,
    auto: str | None = None,
    response_type: str | None = None,
    access_type: str | None = None,
    prompt: str | None = None,
) -> Any:
    return _authorize(
        provider="google",
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        login=login,
        auto=auto,
    )


@app.post("/token")
async def google_token(request: Request) -> JSONResponse:
    return JSONResponse(await _token_exchange(request))


@app.get("/v1/userinfo")
def google_userinfo(request: Request) -> Any:
    token = _bearer(request)
    if not token or token not in _tokens or _tokens[token]["provider"] != "google":
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    return _tokens[token]["profile"]


# TOTP
@app.post("/totp/enroll")
async def totp_enroll(request: Request) -> Any:
    body = await request.json()
    user_id = str(body.get("user_id") or body.get("userId") or "").strip()
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    label = str(body.get("label") or f"locadev:{user_id}")
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    _totp[user_id] = {"secret": secret, "label": label, "created_at": time.time()}
    code = _totp_at(secret)
    return {
        "user_id": user_id,
        "secret": secret,
        "otpauth_url": f"otpauth://totp/{quote(label)}?secret={secret}&issuer=locadev&digits=6&period=30",
        "code": code,
    }


@app.get("/totp/code")
def totp_code(user_id: str) -> Any:
    rec = _totp.get(user_id)
    if not rec:
        return JSONResponse({"error": "not_enrolled", "user_id": user_id}, status_code=404)
    return {"user_id": user_id, "code": _totp_at(rec["secret"]), "period": 30}


@app.post("/totp/verify")
async def totp_verify(request: Request) -> Any:
    body = await request.json()
    user_id = str(body.get("user_id") or body.get("userId") or "").strip()
    code = str(body.get("code") or "").strip()
    rec = _totp.get(user_id)
    if not rec:
        return JSONResponse({"ok": False, "error": "not_enrolled"}, status_code=404)
    return {"ok": _totp_verify(rec["secret"], code), "user_id": user_id}


# Soft passkeys
@app.post("/passkey/register")
async def passkey_register(request: Request) -> Any:
    body = await request.json()
    user_id = str(body.get("user_id") or body.get("userId") or "").strip()
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    cred_id = _b64url(secrets.token_bytes(16))
    _passkeys[cred_id] = {
        "user_id": user_id,
        "secret": secrets.token_bytes(32),
        "created_at": time.time(),
        "name": body.get("name") or f"soft-key-{user_id}",
    }
    return {
        "credential_id": cred_id,
        "user_id": user_id,
        "type": "soft-hmac",
        "note": "Local soft passkey — assert → soft-sign → verify",
    }


@app.post("/passkey/assert")
async def passkey_assert(request: Request) -> Any:
    _purge(_challenges)
    body = await request.json()
    user_id = str(body.get("user_id") or body.get("userId") or "").strip()
    creds = [cid for cid, r in _passkeys.items() if r["user_id"] == user_id]
    if user_id and not creds:
        return JSONResponse({"error": "no_credentials", "user_id": user_id}, status_code=404)
    challenge = _b64url(secrets.token_bytes(32))
    allow = creds or list(_passkeys.keys())
    _challenges[challenge] = {"user_id": user_id or None, "allow": allow, "created_at": time.time()}
    return {
        "challenge": challenge,
        "allowCredentials": [{"id": c, "type": "public-key"} for c in allow],
        "timeout": 60000,
        "rpId": "localhost",
    }


@app.post("/passkey/soft-sign")
async def passkey_soft_sign(request: Request) -> Any:
    body = await request.json()
    challenge = str(body.get("challenge") or "")
    credential_id = str(body.get("credential_id") or body.get("id") or "")
    if credential_id not in _passkeys:
        return JSONResponse({"error": "unknown_credential"}, status_code=404)
    return {
        "credential_id": credential_id,
        "challenge": challenge,
        "signature": _soft_sign(credential_id, challenge),
        "type": "soft-hmac",
    }


@app.post("/passkey/verify")
async def passkey_verify(request: Request) -> Any:
    body = await request.json()
    challenge = str(body.get("challenge") or "")
    credential_id = str(body.get("credential_id") or body.get("id") or "")
    signature = str(body.get("signature") or "")
    meta = _challenges.pop(challenge, None)
    if not meta:
        return JSONResponse({"ok": False, "error": "unknown_challenge"}, status_code=400)
    if credential_id not in _passkeys:
        return JSONResponse({"ok": False, "error": "unknown_credential"}, status_code=400)
    if credential_id not in meta["allow"]:
        return JSONResponse({"ok": False, "error": "credential_not_allowed"}, status_code=400)
    ok = hmac.compare_digest(_soft_sign(credential_id, challenge), signature)
    return {"ok": ok, "user_id": _passkeys[credential_id]["user_id"], "credential_id": credential_id}


@app.get("/passkey/list")
def passkey_list(user_id: str | None = None) -> dict[str, Any]:
    items = [
        {"credential_id": cid, "user_id": r["user_id"], "name": r["name"]}
        for cid, r in _passkeys.items()
        if not user_id or r["user_id"] == user_id
    ]
    return {"passkeys": items}


@app.get("/ui")
def ui() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html><head><title>locadev fake identity</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;line-height:1.4}
 code,pre{background:#f4f4f4;padding:.2rem .4rem;border-radius:4px}
 pre{padding:1rem;overflow:auto}
 section{margin:1.5rem 0;padding:1rem;border:1px solid #ddd;border-radius:8px}
</style></head>
<body>
<h1>locadev fake identity</h1>
<p>OAuth + TOTP + soft passkeys on <code>:8098</code>. <a href="/docs">OpenAPI</a></p>
<section>
 <h2>OAuth</h2>
 <ul>
  <li>GitHub: <code>/login/oauth/authorize</code></li>
  <li>Google: <code>/o/oauth2/v2/auth</code></li>
 </ul>
</section>
<section>
 <h2>TOTP</h2>
 <p>
  <button id="enroll">Enroll alice</button>
  <button id="code">Current code</button>
  <button id="verify">Verify code</button>
 </p>
 <pre id="totp-out">-</pre>
</section>
<section>
 <h2>Soft passkey</h2>
 <p>
  <button id="reg">Register</button>
  <button id="assert">Assert + verify</button>
 </p>
 <pre id="pk-out">-</pre>
</section>
<script>
const out = (id, x) => document.getElementById(id).textContent = typeof x === 'string' ? x : JSON.stringify(x, null, 2);
let lastCode = null, credId = null;
document.getElementById('enroll').onclick = async () => {
  const j = await (await fetch('/totp/enroll', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({user_id:'alice'})})).json();
  lastCode = j.code; out('totp-out', j);
};
document.getElementById('code').onclick = async () => {
  const j = await (await fetch('/totp/code?user_id=alice')).json();
  lastCode = j.code; out('totp-out', j);
};
document.getElementById('verify').onclick = async () => {
  out('totp-out', await (await fetch('/totp/verify', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({user_id:'alice', code: lastCode})})).json());
};
document.getElementById('reg').onclick = async () => {
  const j = await (await fetch('/passkey/register', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({user_id:'alice'})})).json();
  credId = j.credential_id; out('pk-out', j);
};
document.getElementById('assert').onclick = async () => {
  const a = await (await fetch('/passkey/assert', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({user_id:'alice'})})).json();
  const id = credId || (a.allowCredentials[0] && a.allowCredentials[0].id);
  const sig = await (await fetch('/passkey/soft-sign', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({credential_id:id, challenge:a.challenge})})).json();
  const v = await (await fetch('/passkey/verify', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({credential_id:id, challenge:a.challenge, signature: sig.signature})})).json();
  out('pk-out', {assert:a, softSign:sig, verify:v});
};
</script>
</body></html>"""
    )


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "fake-oauth",
        "health": "/health",
        "ui": "/ui",
        "docs": "/docs",
        "github_authorize": "/login/oauth/authorize",
        "google_authorize": "/o/oauth2/v2/auth",
        "totp_enroll": "POST /totp/enroll",
        "passkey_register": "POST /passkey/register",
    }
