"""Shared fixtures and port helpers for locadev smoke tests."""

from __future__ import annotations

import socket

import pytest

# Shared connection constants (host-side)
AZURITE_BLOB = "http://127.0.0.1:10000/devstoreaccount1"
AZURITE_CONN = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10101/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)
SERVICEBUS_CONN = (
    "Endpoint=sb://localhost;"
    "SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;"
    "UseDevelopmentEmulator=true;"
)
BRIDGE = "http://127.0.0.1:8090"
PGLITE = "http://127.0.0.1:5433"
TOPAZ = "http://127.0.0.1:8484"
COSMOS = "http://127.0.0.1:8081"
COSMOS_KEY = (
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
)
AWS_ENDPOINT = "http://127.0.0.1:4566"
MAIL = "http://127.0.0.1:8095"
AISEARCH = "http://127.0.0.1:8800"
TEAMS = "http://127.0.0.1:3979"
TWILIO = "http://127.0.0.1:8099"
GEOCODIO = "http://127.0.0.1:8100"
SUPABASE = "http://127.0.0.1:54321"
SUPABASE_MAILPIT = "http://127.0.0.1:54324"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def require_port(port: int, name: str):
    if not port_open(port):
        pytest.skip(f"{name} not up on :{port}")
