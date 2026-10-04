# fake-stripe (profile `stripe`, port 8102)

A Stripe API stand-in for the parts NexLeeg uses, including Connect direct charges.
Point an app at it with an origin-only base, e.g. `STRIPE_API_BASE=http://127.0.0.1:8102`
and any `sk_test_...` key.

| Area | Endpoints |
|---|---|
| Checkout Sessions | `POST /v1/checkout/sessions`, `GET /v1/checkout/sessions/{id}` (`expand[]=payment_intent`, `expand[]=line_items`), `GET .../line_items`, `POST .../expire` |
| PaymentIntents | `POST /v1/payment_intents`, `GET/POST /v1/payment_intents/{id}`, `POST .../confirm`, `POST .../cancel` |
| Connect | `POST /v1/accounts`, `GET /v1/accounts/{id}` (unknown `acct_` ids resolve as enabled), `POST /v1/account_links`, `POST /v1/accounts/{id}/login_links` |
| Other | `POST /v1/customers`, `GET /v1/customers/{id}`, `POST /v1/refunds`, `GET /v1/events` |

Behaviour that matches Stripe:

- `Stripe-Account` scopes objects: a session created on `acct_X` is a 404 without that header.
- `application_fee_amount` (top level or `payment_intent_data[...]`) is rejected unless the call
  is a direct charge (`Stripe-Account`) or has `transfer_data[destination]`.
- `Idempotency-Key` replays the first response.
- `{CHECKOUT_SESSION_ID}` in `success_url` is replaced on redirect.
- Unknown `/v1/...` routes answer 404 with a Stripe error body, so gaps are loud.

## Paying

`session.url` is a hosted page at `/checkout/pay/{id}` with **Pay with card**, **Pay with bank
debit (async)** and **Cancel**. Without a browser:

```bash
curl -X POST localhost:8102/_test/checkout/sessions/cs_test_.../complete -d '{"async":false}'
curl -X POST localhost:8102/_test/payment_intents/pi_.../succeed   # stands in for Stripe.js
```

Card: `payment_intent.succeeded` then `checkout.session.completed` (paid).
Async: `checkout.session.completed` (unpaid), `payment_intent.succeeded`,
`checkout.session.async_payment_succeeded`. `{"fail":true}` sends `async_payment_failed`.

## Webhooks

Events are signed like Stripe (`Stripe-Signature: t=...,v1=HMAC-SHA256`). Events on a
connected account carry `account` and go to the Connect endpoint.

| Env | Default |
|---|---|
| `STRIPE_WEBHOOK_URL` | unset (no delivery) |
| `STRIPE_CONNECT_WEBHOOK_URL` | falls back to `STRIPE_WEBHOOK_URL` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_locadev_platform` |
| `STRIPE_CONNECT_WEBHOOK_SECRET` | `whsec_locadev_connect` |
| `FAKE_STRIPE_PUBLIC_URL` | `http://127.0.0.1:8102` (base of `session.url`) |

Change them at runtime with `POST /_config {"webhook_url": "...", ...}`. `GET /captured` lists
requests, events and deliveries (with the receiver's status); `DELETE /captured` clears them.

Run natively: `cd fake_stripe && uvicorn app:app --port 8102`.
