"""Fake Stripe API for local development (locadev profile "stripe", port 8102).

Covers the Stripe surface NexLeeg uses, with Connect direct charges:
  Checkout Sessions  POST/GET /v1/checkout/sessions[/{id}[/line_items|/expire]]
  PaymentIntents     POST/GET /v1/payment_intents[/{id}[/confirm|/cancel]]
  Connect            /v1/accounts, /v1/account_links, /v1/accounts/{id}/login_links
  Customers/Refunds  /v1/customers, /v1/refunds
  Events             GET /v1/events
The Stripe-Account header scopes every object to that connected account, like
Stripe: an object created on acct_X is invisible without that header.
application_fee_amount is rejected unless the call is a direct charge
(Stripe-Account header) or a destination charge (transfer_data[destination]).

Hosted Checkout: session.url opens GET /checkout/pay/{id} (Pay card, Pay with
delayed bank debit, Cancel). Scripts can skip the page:
  POST /_test/checkout/sessions/{id}/complete   {"async": false}
  POST /_test/payment_intents/{id}/succeed
Webhooks are signed like Stripe (Stripe-Signature t=..,v1=HMAC-SHA256) and
POSTed to STRIPE_WEBHOOK_URL (platform events) or STRIPE_CONNECT_WEBHOOK_URL
(events with "account", falls back to STRIPE_WEBHOOK_URL). Configure at runtime
with POST /_config. GET /captured shows requests, events and deliveries.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="locadev fake Stripe")

API_VERSION = "2023-10-16"
CONFIG: dict[str, str] = {
    "webhook_url": os.environ.get("STRIPE_WEBHOOK_URL", ""),
    "connect_webhook_url": os.environ.get("STRIPE_CONNECT_WEBHOOK_URL", ""),
    "webhook_secret": os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_locadev_platform"),
    "connect_webhook_secret": os.environ.get(
        "STRIPE_CONNECT_WEBHOOK_SECRET", "whsec_locadev_connect"
    ),
    "public_url": os.environ.get("FAKE_STRIPE_PUBLIC_URL", "http://127.0.0.1:8102").rstrip("/"),
}
OBJECTS: dict[str, dict[str, Any]] = {}
IDEMPOTENCY: dict[tuple, tuple[int, Any]] = {}
EVENTS: list[dict[str, Any]] = []
DELIVERIES: list[dict[str, Any]] = []
REQUESTS: list[dict[str, Any]] = []
SINK: list[dict[str, Any]] = []


class StripeError(Exception):
    def __init__(self, message: str, status: int = 400, param: str | None = None,
                 code: str | None = None, etype: str = "invalid_request_error"):
        self.message, self.status, self.param, self.code, self.etype = (
            message, status, param, code, etype)


@app.exception_handler(StripeError)
async def _stripe_error(_req: Request, exc: StripeError):
    err: dict[str, Any] = {"type": exc.etype, "message": exc.message}
    if exc.param:
        err["param"] = exc.param
    if exc.code:
        err["code"] = exc.code
    return JSONResponse({"error": err}, status_code=exc.status)


# ---------------------------------------------------------------- helpers

def new_id(prefix: str) -> str:
    return f"{prefix}_{'test_' if prefix == 'cs' else ''}{secrets.token_hex(12)}"


def _set_path(root: dict, tokens: list[str], value: str) -> None:
    cur: Any = root
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        if tok == "":  # foo[]=a
            tok = str(len(cur))
        if last:
            cur[tok] = value
        else:
            cur = cur.setdefault(tok, {})


def _listify(node: Any) -> Any:
    if isinstance(node, dict):
        node = {k: _listify(v) for k, v in node.items()}
        if node and all(k.isdigit() for k in node):
            return [node[k] for k in sorted(node, key=int)]
    return node


def parse_form(raw: str) -> dict[str, Any]:
    """Stripe form encoding: a[b][0][c]=1 -> {"a": {"b": [{"c": "1"}]}}."""
    root: dict[str, Any] = {}
    for key, value in parse_qsl(raw, keep_blank_values=True):
        head, _, rest = key.partition("[")
        tokens = [head] + ([t for t in rest.rstrip("]").split("][")] if rest else [])
        _set_path(root, tokens, value)
    return _listify(root)


def as_int(value: Any, param: str, required: bool = False) -> int | None:
    if value in (None, ""):
        if required:
            raise StripeError(f"Missing required param: {param}.", param=param,
                              code="parameter_missing")
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise StripeError(f"Invalid integer: {value}", param=param, code="parameter_invalid_integer")


def public(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def get_obj(oid: str, prefix: str, account: str | None, kind: str) -> dict[str, Any]:
    obj = OBJECTS.get(oid)
    if not obj or not oid.startswith(prefix) or obj.get("_account") != account:
        raise StripeError(f"No such {kind}: '{oid}'", status=404, param="id",
                          code="resource_missing")
    return obj


def check_application_fee(fee: int | None, amount: int, account: str | None,
                          transfer: Any, param: str) -> None:
    if fee is None:
        return
    if not account and not (isinstance(transfer, dict) and transfer.get("destination")):
        raise StripeError(
            "Can only apply an application_fee_amount when the PaymentIntent is attempting "
            "a direct payment (using an OAuth key or Stripe-Account header) or destination "
            "payment (using `transfer_data[destination]`).", param=param)
    if fee < 0 or fee > amount:
        raise StripeError("application_fee_amount must be between 0 and the charge amount.",
                          param=param)


def sign(payload: str, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


async def emit(etype: str, obj: dict[str, Any], account: str | None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": new_id("evt"), "object": "event", "api_version": API_VERSION,
        "created": int(time.time()), "type": etype, "livemode": False,
        "pending_webhooks": 1, "request": {"id": None, "idempotency_key": None},
        "data": {"object": public(obj)},
    }
    if account:
        event["account"] = account
    EVENTS.append(event)
    if account:
        url = CONFIG["connect_webhook_url"] or CONFIG["webhook_url"]
        secret = CONFIG["connect_webhook_secret"]
    else:
        url, secret = CONFIG["webhook_url"], CONFIG["webhook_secret"]
    delivery: dict[str, Any] = {"event": event["id"], "type": etype, "account": account,
                                "url": url or None, "status": None}
    if url:
        payload = json.dumps(event, separators=(",", ":"))
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(url, content=payload, headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Stripe-Signature": sign(payload, secret),
                    "User-Agent": "Stripe/1.0 (+https://stripe.com/docs/webhooks)"})
            delivery["status"] = res.status_code
            delivery["body"] = res.text[:300]
        except httpx.HTTPError as exc:
            delivery["error"] = str(exc)
    DELIVERIES.append(delivery)
    return event


# ---------------------------------------------------------------- middleware

@app.middleware("http")
async def stripe_request(request: Request, call_next):
    if not request.url.path.startswith("/v1/"):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not (auth.startswith("Bearer sk_") or auth.startswith("Bearer rk_") or auth.startswith("Basic ")):
        return JSONResponse({"error": {"type": "invalid_request_error",
                                       "message": "Invalid API Key provided."}}, status_code=401)
    account = request.headers.get("stripe-account") or None
    raw = (await request.body()).decode() if request.method == "POST" else ""
    request.state.params = parse_form(raw) if raw else parse_form(request.url.query)
    request.state.account = account
    REQUESTS.append({"method": request.method, "path": request.url.path, "account": account,
                     "params": request.state.params, "at": int(time.time())})
    key = request.headers.get("idempotency-key")
    cache_key = (account, key, request.method, request.url.path) if key and request.method == "POST" else None
    if cache_key and cache_key in IDEMPOTENCY:
        status, body = IDEMPOTENCY[cache_key]
        return JSONResponse(body, status_code=status, headers={"Idempotent-Replayed": "true"})
    response = await call_next(request)
    if cache_key and response.status_code < 500:
        chunks = [c async for c in response.body_iterator]
        body = json.loads(b"".join(chunks) or b"null")
        IDEMPOTENCY[cache_key] = (response.status_code, body)
        return JSONResponse(body, status_code=response.status_code)
    return response


def params(req: Request) -> dict[str, Any]:
    return req.state.params


def expand(p: dict[str, Any]) -> list[str]:
    e = p.get("expand") or []
    return e if isinstance(e, list) else [e]


# ---------------------------------------------------------------- checkout sessions

def _line_items(p: dict[str, Any]) -> list[dict[str, Any]]:
    raw = p.get("line_items")
    if not raw:
        raise StripeError("Missing required param: line_items.", param="line_items",
                          code="parameter_missing")
    items = []
    for i, li in enumerate(raw if isinstance(raw, list) else [raw]):
        qty = as_int(li.get("quantity", "1"), f"line_items[{i}][quantity]") or 1
        pd = li.get("price_data") or {}
        unit = as_int(pd.get("unit_amount"), f"line_items[{i}][price_data][unit_amount]")
        if unit is None:
            raise StripeError("You must specify either price or price_data.unit_amount.",
                              param=f"line_items[{i}][price_data]")
        name = (pd.get("product_data") or {}).get("name") or "Item"
        items.append({"id": new_id("li"), "object": "item", "description": name,
                      "quantity": qty, "currency": pd.get("currency", "usd"),
                      "amount_subtotal": unit * qty, "amount_total": unit * qty,
                      "price": {"object": "price", "unit_amount": unit,
                                "currency": pd.get("currency", "usd"),
                                "product_data": pd.get("product_data")}})
    return items


def _session_view(s: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    view = public(s)
    if "payment_intent" in expand(p) and s.get("payment_intent"):
        view["payment_intent"] = public(OBJECTS[s["payment_intent"]])
    if "line_items" in expand(p):
        view["line_items"] = {"object": "list", "data": s["_line_items"], "has_more": False}
    return view


@app.post("/v1/checkout/sessions")
async def create_session(request: Request):
    p, account = params(request), request.state.account
    mode = p.get("mode")
    if mode not in ("payment", "setup", "subscription"):
        raise StripeError("Missing required param: mode.", param="mode", code="parameter_missing")
    if not p.get("success_url") and p.get("ui_mode", "hosted") == "hosted":
        raise StripeError("Missing required param: success_url.", param="success_url",
                          code="parameter_missing")
    items = _line_items(p)
    total = sum(li["amount_total"] for li in items)
    pid = p.get("payment_intent_data") or {}
    fee = as_int(pid.get("application_fee_amount"), "payment_intent_data[application_fee_amount]")
    check_application_fee(fee, total, account, pid.get("transfer_data"),
                          "payment_intent_data[application_fee_amount]")
    sid = new_id("cs")
    now = int(time.time())
    session = {
        "id": sid, "object": "checkout.session", "mode": mode, "status": "open",
        "payment_status": "unpaid", "amount_subtotal": total, "amount_total": total,
        "currency": items[0]["currency"], "metadata": p.get("metadata") or {},
        "client_reference_id": p.get("client_reference_id"),
        "customer": p.get("customer"), "customer_email": p.get("customer_email"),
        "success_url": p.get("success_url"), "cancel_url": p.get("cancel_url"),
        "url": f"{CONFIG['public_url']}/checkout/pay/{sid}", "payment_intent": None,
        "livemode": False, "created": now, "expires_at": now + 86400,
        "_account": account, "_line_items": items, "_payment_intent_data": pid,
        "_application_fee_amount": fee,
    }
    OBJECTS[sid] = session
    return _session_view(session, p)


@app.get("/v1/checkout/sessions/{sid}")
async def get_session(sid: str, request: Request):
    s = get_obj(sid, "cs_", request.state.account, "checkout.session")
    return _session_view(s, params(request))


@app.get("/v1/checkout/sessions/{sid}/line_items")
async def session_line_items(sid: str, request: Request):
    s = get_obj(sid, "cs_", request.state.account, "checkout.session")
    return {"object": "list", "data": s["_line_items"], "has_more": False,
            "url": f"/v1/checkout/sessions/{sid}/line_items"}


@app.post("/v1/checkout/sessions/{sid}/expire")
async def expire_session(sid: str, request: Request):
    s = get_obj(sid, "cs_", request.state.account, "checkout.session")
    if s["status"] != "open":
        raise StripeError("Only Checkout Sessions with a status of open can be expired.")
    s.update(status="expired", url=None)
    await emit("checkout.session.expired", s, s["_account"])
    return public(s)


def _new_pi(amount: int, account: str | None, *, currency: str = "usd", fee: int | None = None,
            metadata: dict | None = None, description: str | None = None,
            status: str = "requires_payment_method", **extra: Any) -> dict[str, Any]:
    pid = new_id("pi")
    pi = {"id": pid, "object": "payment_intent", "amount": amount,
          "amount_received": 0, "currency": currency, "application_fee_amount": fee,
          "client_secret": f"{pid}_secret_{secrets.token_hex(8)}", "status": status,
          "metadata": metadata or {}, "description": description, "livemode": False,
          "created": int(time.time()), "latest_charge": None, "last_payment_error": None,
          "payment_method": None, "_account": account, **extra}
    OBJECTS[pid] = pi
    return pi


def _succeed_pi(pi: dict[str, Any]) -> None:
    pi.update(status="succeeded", amount_received=pi["amount"],
              latest_charge=new_id("ch"), payment_method=pi.get("payment_method") or new_id("pm"))


async def complete_session(s: dict[str, Any], delayed: bool = False) -> dict[str, Any]:
    if s["status"] != "open":
        raise StripeError(f"Checkout Session is {s['status']}, not open.")
    pid = s["_payment_intent_data"]
    pi = _new_pi(s["amount_total"], s["_account"], currency=s["currency"],
                 fee=s["_application_fee_amount"], metadata=pid.get("metadata") or {},
                 description=pid.get("description"), status="processing")
    s.update(status="complete", payment_intent=pi["id"], url=None)
    if delayed:
        # Bank debit: the session completes unpaid, then the funds arrive.
        await emit("checkout.session.completed", s, s["_account"])
        _succeed_pi(pi)
        s["payment_status"] = "paid"
        await emit("payment_intent.succeeded", pi, s["_account"])
        await emit("checkout.session.async_payment_succeeded", s, s["_account"])
    else:
        _succeed_pi(pi)
        s["payment_status"] = "paid"
        await emit("payment_intent.succeeded", pi, s["_account"])
        await emit("checkout.session.completed", s, s["_account"])
    return s


async def fail_session_async(s: dict[str, Any]) -> dict[str, Any]:
    if s["status"] != "open":
        raise StripeError(f"Checkout Session is {s['status']}, not open.")
    pi = _new_pi(s["amount_total"], s["_account"], currency=s["currency"],
                 fee=s["_application_fee_amount"],
                 metadata=(s["_payment_intent_data"].get("metadata") or {}), status="processing")
    s.update(status="complete", payment_intent=pi["id"], url=None)
    await emit("checkout.session.completed", s, s["_account"])
    pi.update(status="requires_payment_method",
              last_payment_error={"code": "payment_method_insufficient_funds"})
    await emit("checkout.session.async_payment_failed", s, s["_account"])
    return s


def _success_redirect(s: dict[str, Any]) -> str:
    return (s.get("success_url") or "/").replace("{CHECKOUT_SESSION_ID}", s["id"])


@app.get("/checkout/pay/{sid}", response_class=HTMLResponse)
async def hosted_checkout(sid: str):
    s = OBJECTS.get(sid)
    if not s or not sid.startswith("cs_"):
        return HTMLResponse("<h1>Checkout session not found</h1>", status_code=404)
    rows = "".join(
        f"<tr><td>{html.escape(li['description'])} x{li['quantity']}</td>"
        f"<td style='text-align:right'>${li['amount_total'] / 100:.2f}</td></tr>"
        for li in s["_line_items"])
    fee = s["_application_fee_amount"]
    state = "" if s["status"] == "open" else f"<p><b>Session is {s['status']}.</b></p>"
    return f"""<!doctype html><html><head><title>locadev Checkout</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto">
<h2>locadev fake Stripe Checkout</h2>{state}
<p>Account: <code>{html.escape(s['_account'] or 'platform')}</code>
{f' · application fee ${fee / 100:.2f}' if fee is not None else ''}</p>
<table style="width:100%">{rows}<tr><td><b>Total</b></td>
<td style="text-align:right"><b>${s['amount_total'] / 100:.2f}</b></td></tr></table>
<form method="post"><input type="hidden" name="outcome" value="paid">
<button id="pay" style="margin-top:16px;width:100%;padding:12px">Pay with card 4242</button></form>
<form method="post"><input type="hidden" name="outcome" value="async">
<button id="pay-async" style="margin-top:8px;width:100%;padding:12px">Pay with bank debit (async)</button></form>
<form method="post"><input type="hidden" name="outcome" value="cancel">
<button id="cancel" style="margin-top:8px;width:100%;padding:8px">Cancel</button></form>
</body></html>"""


@app.post("/checkout/pay/{sid}")
async def hosted_checkout_submit(sid: str, request: Request):
    s = OBJECTS.get(sid)
    if not s or not sid.startswith("cs_"):
        return HTMLResponse("<h1>Checkout session not found</h1>", status_code=404)
    outcome = parse_form((await request.body()).decode()).get("outcome", "paid")
    if outcome == "cancel":
        return RedirectResponse(s.get("cancel_url") or "/", status_code=303)
    await complete_session(s, delayed=outcome == "async")
    return RedirectResponse(_success_redirect(s), status_code=303)


@app.post("/_test/checkout/sessions/{sid}/complete")
async def test_complete_session(sid: str, request: Request):
    s = OBJECTS.get(sid)
    if not s or not sid.startswith("cs_"):
        raise StripeError(f"No such checkout.session: '{sid}'", status=404)
    body = await request.body()
    opts = json.loads(body) if body else {}
    if opts.get("fail"):
        await fail_session_async(s)
    else:
        await complete_session(s, delayed=bool(opts.get("async")))
    return {**public(s), "redirect_url": _success_redirect(s)}


# ---------------------------------------------------------------- payment intents

@app.post("/v1/payment_intents")
async def create_pi(request: Request):
    p, account = params(request), request.state.account
    amount = as_int(p.get("amount"), "amount", required=True)
    if amount < 50:
        raise StripeError("Amount must be at least $0.50 usd", param="amount",
                          code="amount_too_small")
    fee = as_int(p.get("application_fee_amount"), "application_fee_amount")
    check_application_fee(fee, amount, account, p.get("transfer_data"), "application_fee_amount")
    pi = _new_pi(amount, account, currency=p.get("currency", "usd"), fee=fee,
                 metadata=p.get("metadata") or {}, description=p.get("description"),
                 customer=p.get("customer"), transfer_data=p.get("transfer_data"))
    if p.get("payment_method"):
        pi["payment_method"] = p["payment_method"]
    if str(p.get("confirm", "")).lower() == "true":
        await _confirm(pi, p.get("payment_method"))
    return public(pi)


@app.get("/v1/payment_intents/{pid}")
async def get_pi(pid: str, request: Request):
    return public(get_obj(pid, "pi_", request.state.account, "payment_intent"))


@app.post("/v1/payment_intents/{pid}")
async def update_pi(pid: str, request: Request):
    p = params(request)
    pi = get_obj(pid, "pi_", request.state.account, "payment_intent")
    if "metadata" in p:
        md = dict(pi["metadata"])
        for k, v in (p["metadata"] or {}).items():
            if v == "":
                md.pop(k, None)
            else:
                md[k] = v
        pi["metadata"] = md
    for field in ("description", "receipt_email"):
        if field in p:
            pi[field] = p[field]
    if "amount" in p:
        if pi["status"] == "succeeded":
            raise StripeError("This PaymentIntent's amount could not be updated because it has a status of succeeded.")
        pi["amount"] = as_int(p["amount"], "amount")
    return public(pi)


async def _confirm(pi: dict[str, Any], pm: str | None) -> None:
    if pi["status"] in ("succeeded", "canceled"):
        raise StripeError(f"This PaymentIntent's status is {pi['status']}.",
                          code="payment_intent_unexpected_state")
    pm = pm or pi.get("payment_method") or "pm_card_visa"
    pi["payment_method"] = pm
    if "decline" in pm.lower():
        pi.update(status="requires_payment_method",
                  last_payment_error={"code": "card_declined", "message": "Your card was declined."})
        await emit("payment_intent.payment_failed", pi, pi["_account"])
        return
    _succeed_pi(pi)
    await emit("payment_intent.succeeded", pi, pi["_account"])


@app.post("/v1/payment_intents/{pid}/confirm")
async def confirm_pi(pid: str, request: Request):
    p = params(request)
    pi = get_obj(pid, "pi_", request.state.account, "payment_intent")
    await _confirm(pi, p.get("payment_method"))
    return public(pi)


@app.post("/v1/payment_intents/{pid}/cancel")
async def cancel_pi(pid: str, request: Request):
    pi = get_obj(pid, "pi_", request.state.account, "payment_intent")
    if pi["status"] == "succeeded":
        raise StripeError("You cannot cancel this PaymentIntent because it has a status of succeeded.")
    pi["status"] = "canceled"
    await emit("payment_intent.canceled", pi, pi["_account"])
    return public(pi)


@app.post("/_test/payment_intents/{pid}/succeed")
async def test_succeed_pi(pid: str):
    """Stands in for Stripe.js confirming the card in the browser."""
    pi = OBJECTS.get(pid)
    if not pi or not pid.startswith("pi_"):
        raise StripeError(f"No such payment_intent: '{pid}'", status=404)
    await _confirm(pi, "pm_card_visa")
    return public(pi)


# ---------------------------------------------------------------- connect, customers, refunds

@app.post("/v1/accounts")
async def create_account(request: Request):
    p = params(request)
    aid = new_id("acct")
    acct = {"id": aid, "object": "account", "type": p.get("type", "express"),
            "country": p.get("country", "US"), "email": p.get("email"),
            "business_type": p.get("business_type"), "metadata": p.get("metadata") or {},
            "charges_enabled": True, "payouts_enabled": True, "details_submitted": True,
            "capabilities": {"card_payments": "active", "transfers": "active"},
            "requirements": {"currently_due": [], "disabled_reason": None},
            "created": int(time.time()), "_account": None}
    OBJECTS[aid] = acct
    return public(acct)


@app.get("/v1/accounts/{aid}")
async def get_account(aid: str):
    acct = OBJECTS.get(aid)
    if not acct and aid.startswith("acct_"):
        # Accounts seeded straight into the app's database still resolve.
        acct = {"id": aid, "object": "account", "type": "express", "country": "US",
                "charges_enabled": True, "payouts_enabled": True, "details_submitted": True,
                "capabilities": {"card_payments": "active", "transfers": "active"},
                "requirements": {"currently_due": [], "disabled_reason": None},
                "metadata": {}, "_account": None}
        OBJECTS[aid] = acct
    if not acct:
        raise StripeError(f"No such account: '{aid}'", status=404, code="resource_missing")
    return public(acct)


@app.post("/v1/account_links")
async def account_link(request: Request):
    p = params(request)
    if not p.get("account"):
        raise StripeError("Missing required param: account.", param="account")
    return {"object": "account_link", "created": int(time.time()),
            "expires_at": int(time.time()) + 300,
            "url": p.get("return_url") or f"{CONFIG['public_url']}/connect/{p['account']}"}


@app.post("/v1/accounts/{aid}/login_links")
async def login_link(aid: str):
    return {"object": "login_link", "created": int(time.time()),
            "url": f"{CONFIG['public_url']}/connect/{aid}"}


@app.post("/v1/customers")
async def create_customer(request: Request):
    p = params(request)
    cid = new_id("cus")
    cus = {"id": cid, "object": "customer", "email": p.get("email"), "name": p.get("name"),
           "metadata": p.get("metadata") or {}, "created": int(time.time()),
           "_account": request.state.account}
    OBJECTS[cid] = cus
    return public(cus)


@app.get("/v1/customers/{cid}")
async def get_customer(cid: str, request: Request):
    return public(get_obj(cid, "cus_", request.state.account, "customer"))


@app.post("/v1/refunds")
async def create_refund(request: Request):
    p, account = params(request), request.state.account
    pi = get_obj(p.get("payment_intent") or "", "pi_", account, "payment_intent")
    if pi["status"] != "succeeded":
        raise StripeError("This PaymentIntent does not have a successful charge to refund.")
    amount = as_int(p.get("amount"), "amount") or pi["amount"]
    rid = new_id("re")
    refund = {"id": rid, "object": "refund", "amount": amount, "currency": pi["currency"],
              "payment_intent": pi["id"], "charge": pi["latest_charge"], "status": "succeeded",
              "metadata": p.get("metadata") or {}, "created": int(time.time()),
              "_account": account}
    OBJECTS[rid] = refund
    charge = {"id": pi["latest_charge"], "object": "charge", "amount": pi["amount"],
              "amount_refunded": amount, "payment_intent": pi["id"], "refunded": amount >= pi["amount"],
              "metadata": pi["metadata"]}
    await emit("charge.refunded", charge, account)
    return public(refund)


@app.get("/v1/events")
async def list_events(request: Request):
    p = params(request)
    data = [e for e in reversed(EVENTS) if not p.get("type") or e["type"] == p["type"]]
    return {"object": "list", "data": data[: as_int(p.get("limit"), "limit") or 10],
            "has_more": False, "url": "/v1/events"}


@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "DELETE"])
async def unknown(rest: str, request: Request):
    raise StripeError(f"Unrecognized request URL ({request.method}: /v1/{rest}). "
                      "locadev fake Stripe does not emulate this endpoint yet.", status=404)


# ---------------------------------------------------------------- local helpers

@app.get("/health")
async def health():
    return {"ok": True, "service": "fake-stripe", "webhook_url": CONFIG["webhook_url"] or None,
            "connect_webhook_url": CONFIG["connect_webhook_url"] or None}


@app.post("/_config")
async def set_config(request: Request):
    body = await request.json()
    for k in CONFIG:
        if k in body:
            CONFIG[k] = str(body[k] or "").rstrip("/") if k.endswith("url") else str(body[k] or "")
    return {k: (v if "secret" not in k else "set" if v else "") for k, v in CONFIG.items()}


@app.get("/captured")
async def captured():
    return {"requests": REQUESTS[-200:], "events": EVENTS[-200:], "deliveries": DELIVERIES[-200:]}


@app.delete("/captured")
async def clear_captured():
    REQUESTS.clear(); EVENTS.clear(); DELIVERIES.clear(); SINK.clear()
    return {"ok": True}


@app.post("/_sink/{endpoint}")
async def sink(endpoint: str, request: Request):
    """Webhook receiver for self-tests: verifies the signature like a consumer would."""
    payload = (await request.body()).decode()
    header = request.headers.get("stripe-signature", "")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    secret = CONFIG["connect_webhook_secret" if endpoint == "connect" else "webhook_secret"]
    expected = hmac.new(secret.encode(), f"{parts.get('t')}.{payload}".encode(),
                        hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expected, parts.get("v1", ""))
    SINK.append({"endpoint": endpoint, "verified": ok, "event": json.loads(payload)})
    return JSONResponse({"received": ok}, status_code=200 if ok else 400)


@app.get("/_sink")
async def sink_list():
    return SINK
