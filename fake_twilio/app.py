"""Fake Twilio Programmable Messaging — nothing leaves the machine.

Implements the one call most apps make:
  POST /2010-04-01/Accounts/{AccountSid}/Messages.json   (form-encoded To/From/Body)
Any AccountSid / auth token is accepted. Inspect traffic with GET /captured.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="locadev-fake-twilio")

_captured: list[dict[str, Any]] = []


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "captured": len(_captured)}


@app.post("/2010-04-01/Accounts/{account_sid}/Messages.json")
async def create_message(account_sid: str, request: Request) -> JSONResponse:
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        form: dict[str, Any] = await request.json()
    else:
        form = dict(await request.form())
    to = str(form.get("To") or "")
    body = str(form.get("Body") or "")
    if not to or not (body or form.get("MediaUrl")):
        # Same shape as Twilio's 400 for a missing parameter
        return JSONResponse(
            status_code=400,
            content={"code": 21604, "message": "A 'To' phone number and 'Body' are required.", "status": 400},
        )
    sid = "SM" + uuid.uuid4().hex
    now = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    msg = {
        "sid": sid,
        "account_sid": account_sid,
        "to": to,
        "from": str(form.get("From") or form.get("MessagingServiceSid") or ""),
        "body": body,
        "status": "queued",
        "direction": "outbound-api",
        "num_segments": str(max(1, (len(body) + 159) // 160)),
        "date_created": now,
        "date_updated": now,
        "uri": f"/2010-04-01/Accounts/{account_sid}/Messages/{sid}.json",
    }
    _captured.append(msg)
    return JSONResponse(status_code=201, content=msg)


@app.get("/2010-04-01/Accounts/{account_sid}/Messages/{sid}.json")
def get_message(account_sid: str, sid: str) -> JSONResponse:
    for m in _captured:
        if m["sid"] == sid:
            return JSONResponse(content={**m, "status": "delivered"})
    return JSONResponse(status_code=404, content={"code": 20404, "message": "Not found", "status": 404})


@app.get("/captured")
def get_captured() -> list[dict[str, Any]]:
    return list(_captured)


@app.delete("/captured")
def clear_captured() -> dict[str, int]:
    n = len(_captured)
    _captured.clear()
    return {"cleared": n}
