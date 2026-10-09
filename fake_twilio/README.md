# fake-twilio (profile `sms`)

Local stand-in for Twilio Programmable Messaging on **http://127.0.0.1:8099**. Nothing leaves the machine.

| Route | Purpose |
|-------|---------|
| `POST /2010-04-01/Accounts/{sid}/Messages.json` | Send SMS (form-encoded `To`, `From`, `Body`; JSON also accepted) → 201 with a Twilio-shaped message |
| `GET /2010-04-01/Accounts/{sid}/Messages/{msgSid}.json` | Fetch a sent message (reports `delivered`) |
| `GET /captured` / `DELETE /captured` | Inspect / clear captured messages |
| `GET /health` | Health |

Any Account SID and auth token are accepted (Basic auth is not checked).

```bash
./scripts/start.sh sms
curl -s -u ACtest:test -X POST http://127.0.0.1:8099/2010-04-01/Accounts/ACtest/Messages.json \
  --data-urlencode To=+15555550100 --data-urlencode From=+15555550199 --data-urlencode Body='hello'
curl -s http://127.0.0.1:8099/captured
```

Consumer wiring: point your Twilio base URL at `http://127.0.0.1:8099` (e.g. `TWILIO_API_BASE`), or from another compose service at `http://twilio:8099`.

**Limits:** Messaging `Messages.json` only (no Voice, Verify, Conversations, status callbacks, or signature checks). Messages live in memory and reset on restart.
