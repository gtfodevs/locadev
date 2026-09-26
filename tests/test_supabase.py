"""Supabase profile smoke: API gateway, auth health, and Mailpit.

Keys come from `scripts/supabase.sh env` (local demo keys); the test only
needs the anon/publishable key, read from SUPABASE_ANON_KEY if set.
"""

import os
import subprocess

import httpx

from conftest import SUPABASE, SUPABASE_MAILPIT, port_open, require_port


def _anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY", "")
    if key:
        return key
    try:
        out = subprocess.run(
            ["bash", os.path.join(os.path.dirname(__file__), "..", "scripts", "supabase.sh"), "env"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        if line.startswith("ANON_KEY=") or line.startswith("PUBLISHABLE_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def test_supabase_auth_health_and_mailpit():
    require_port(54321, "Supabase API")
    key = _anon_key()
    headers = {"apikey": key} if key else {}
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{SUPABASE}/auth/v1/health", headers=headers)
        assert r.status_code == 200, r.text
        if key:
            rest = c.get(f"{SUPABASE}/rest/v1/", headers={**headers, "Authorization": f"Bearer {key}"})
            assert rest.status_code < 500
    if port_open(54324):
        with httpx.Client(timeout=10.0) as c:
            assert c.get(f"{SUPABASE_MAILPIT}/api/v1/messages").status_code == 200
