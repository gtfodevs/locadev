"""fake-stripe: Connect direct-charge Checkout Sessions, PaymentIntents and signed webhooks."""
import httpx

from conftest import STRIPE, require_port

KEY = {"Authorization": "Bearer sk_test_locadev"}
ACCT = "acct_locadev_league1"


def _config(c):
    c.delete(f"{STRIPE}/captured")
    c.post(f"{STRIPE}/_config", json={
        "webhook_url": f"{STRIPE}/_sink/platform",
        "connect_webhook_url": f"{STRIPE}/_sink/connect",
    })


def _session(c, account=ACCT, fee="150", key=None):
    headers = {**KEY, **({"Stripe-Account": account} if account else {})}
    if key:
        headers["Idempotency-Key"] = key
    return c.post(f"{STRIPE}/v1/checkout/sessions", headers=headers, data={
        "mode": "payment",
        "success_url": "http://localhost:8006/registration/paid?registration=r1&session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": "http://localhost:8006/cancel",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": "10000",
        "line_items[0][price_data][product_data][name]": "Spring registration",
        "line_items[1][quantity]": "1",
        "line_items[1][price_data][currency]": "usd",
        "line_items[1][price_data][unit_amount]": "500",
        "line_items[1][price_data][product_data][name]": "Stream add-on",
        "payment_intent_data[application_fee_amount]": fee,
        "payment_intent_data[metadata][registration_id]": "r1",
        "metadata[payment_type]": "registration",
        "metadata[registration_id]": "r1",
    })


def test_checkout_session_direct_charge_and_webhooks():
    require_port(8102, "fake-stripe")
    with httpx.Client(timeout=20.0) as c:
        _config(c)
        assert c.post(f"{STRIPE}/v1/checkout/sessions", data={}).status_code == 401
        no_acct = _session(c, account=None)
        assert no_acct.status_code == 400 and "application_fee_amount" in no_acct.json()["error"]["message"]
        s = _session(c, key="reg-r1").json()
        assert s["id"].startswith("cs_test_") and s["amount_total"] == 10500 and s["status"] == "open"
        assert s["metadata"]["registration_id"] == "r1" and s["url"]
        assert _session(c, key="reg-r1").json()["id"] == s["id"]  # idempotent replay
        # Scoped to the connected account like Stripe.
        assert c.get(f"{STRIPE}/v1/checkout/sessions/{s['id']}", headers=KEY).status_code == 404
        page = c.get(s["url"])
        assert page.status_code == 200 and "Stream add-on" in page.text
        done = c.post(f"{STRIPE}/checkout/pay/{s['id']}", data={"outcome": "paid"})
        assert done.status_code == 303 and f"session_id={s['id']}" in done.headers["location"]
        got = c.get(f"{STRIPE}/v1/checkout/sessions/{s['id']}",
                    params={"expand[]": "payment_intent"},
                    headers={**KEY, "Stripe-Account": ACCT}).json()
        assert got["status"] == "complete" and got["payment_status"] == "paid"
        assert got["payment_intent"]["application_fee_amount"] == 150
        assert got["payment_intent"]["status"] == "succeeded"
        sink = c.get(f"{STRIPE}/_sink").json()
        types = [(row["endpoint"], row["event"]["type"]) for row in sink]
        assert ("connect", "checkout.session.completed") in types
        assert all(row["verified"] for row in sink)
        assert all(row["event"]["account"] == ACCT for row in sink)


def test_checkout_async_payment_succeeded():
    require_port(8102, "fake-stripe")
    with httpx.Client(timeout=20.0) as c:
        _config(c)
        s = _session(c).json()
        out = c.post(f"{STRIPE}/_test/checkout/sessions/{s['id']}/complete", json={"async": True}).json()
        assert out["payment_status"] == "paid"
        sink = [row["event"] for row in c.get(f"{STRIPE}/_sink").json()]
        types = [e["type"] for e in sink]
        assert types.index("checkout.session.completed") < types.index("checkout.session.async_payment_succeeded")
        first = next(e for e in sink if e["type"] == "checkout.session.completed")
        assert first["data"]["object"]["payment_status"] == "unpaid"


def test_payment_intent_direct_charge():
    require_port(8102, "fake-stripe")
    with httpx.Client(timeout=20.0) as c:
        _config(c)
        h = {**KEY, "Stripe-Account": ACCT}
        pi = c.post(f"{STRIPE}/v1/payment_intents", headers=h, data={
            "amount": "25500", "currency": "usd", "application_fee_amount": "2550",
            "metadata[payment_type]": "sponsorship",
        }).json()
        assert pi["status"] == "requires_payment_method" and pi["client_secret"].startswith(pi["id"])
        c.post(f"{STRIPE}/v1/payment_intents/{pi['id']}", headers=h, data={"metadata[payment_id]": "p1"})
        c.post(f"{STRIPE}/_test/payment_intents/{pi['id']}/succeed")
        got = c.get(f"{STRIPE}/v1/payment_intents/{pi['id']}", headers=h).json()
        assert got["status"] == "succeeded" and got["metadata"] == {"payment_type": "sponsorship", "payment_id": "p1"}
        sink = c.get(f"{STRIPE}/_sink").json()
        assert [r["event"]["type"] for r in sink] == ["payment_intent.succeeded"] and sink[0]["verified"]
