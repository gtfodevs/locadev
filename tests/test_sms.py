import httpx

from conftest import TWILIO, require_port


def test_twilio_message_capture():
    require_port(8099, "fake-twilio")
    with httpx.Client(timeout=10.0) as c:
        c.delete(f"{TWILIO}/captured")
        r = c.post(
            f"{TWILIO}/2010-04-01/Accounts/ACtest/Messages.json",
            auth=("ACtest", "token"),
            data={"To": "+15555550100", "From": "+15555550199", "Body": "locadev sms smoke"},
        )
        assert r.status_code == 201
        msg = r.json()
        assert msg["sid"].startswith("SM") and msg["status"] == "queued"
        got = c.get(f"{TWILIO}/2010-04-01/Accounts/ACtest/Messages/{msg['sid']}.json")
        assert got.status_code == 200
        cap = c.get(f"{TWILIO}/captured").json()
        assert any(m["body"] == "locadev sms smoke" for m in cap)
        bad = c.post(f"{TWILIO}/2010-04-01/Accounts/ACtest/Messages.json", data={"Body": "x"})
        assert bad.status_code == 400
