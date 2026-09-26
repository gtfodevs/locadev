"""Azure OpenAI / Foundry surface for locadev.

Presents the same URL shape as Azure OpenAI so clients can swap endpoint only.
Backends: fake (default), ollama, claude-cli (chat, host-only).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="locadev-bridge")

CHAT_BACKEND = os.environ.get("CHAT_BACKEND", "fake")
EMB_BACKEND = os.environ.get("EMB_BACKEND", "fake")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://host.docker.internal:11434").rstrip("/")
OLLAMA_CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1536"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "")
CLAUDE_TIMEOUT_S = int(os.environ.get("CLAUDE_TIMEOUT_S", "120"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "chat_backend": CHAT_BACKEND,
        "emb_backend": EMB_BACKEND,
        "embed_dim": EMBED_DIM,
    }


def _usage_from_text(prompt: str, completion: str) -> dict[str, int]:
    pt = max(1, len(prompt.split()))
    ct = max(1, len(completion.split()))
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "user").capitalize()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )
        parts.append(f"{role}: {content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            return str(content)
    return ""


async def _chat_fake(deployment: str, messages: list[dict[str, Any]]) -> str:
    last = _last_user_text(messages)[:200]
    return f"FAKE_FOUNDRY[{deployment}]: {last}"


async def _chat_ollama(messages: list[dict[str, Any]]) -> str:
    ollama_messages = []
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )
        ollama_messages.append({"role": m.get("role", "user"), "content": content})
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": ollama_messages,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        r.raise_for_status()
        return r.json()["message"]["content"]


def _chat_claude_cli(messages: list[dict[str, Any]]) -> str:
    prompt = _flatten_messages(messages)
    cmd = ["claude", "-p"]
    if CLAUDE_MODEL:
        cmd.extend(["--model", CLAUDE_MODEL])
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError("claude CLI not found on PATH (host-only mode)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timed out after {CLAUDE_TIMEOUT_S}s") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown").strip()
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {err[:500]}")
    return (proc.stdout or "").strip()


async def _generate_chat(deployment: str, messages: list[dict[str, Any]]) -> str:
    backend = CHAT_BACKEND
    try:
        if backend == "fake":
            return await _chat_fake(deployment, messages)
        if backend == "ollama":
            return await _chat_ollama(messages)
        if backend == "claude-cli":
            return _chat_claude_cli(messages)
        raise RuntimeError(f"unknown CHAT_BACKEND={backend!r}")
    except Exception as e:
        raise RuntimeError(f"chat backend ({backend}) failed: {e}") from e


def _completion_object(
    deployment: str, content: str, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    prompt = _flatten_messages(messages)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": deployment,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_from_text(prompt, content),
    }


def _stream_frames(deployment: str, content: str) -> list[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        body = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(body)}\n\n"

    frames = [
        chunk({"role": "assistant"}),
        chunk({"content": content}),
        chunk({}, finish="stop"),
        "data: [DONE]\n\n",
    ]
    return frames


@app.post("/openai/deployments/{deployment}/chat/completions")
async def chat_completions(deployment: str, request: Request) -> Any:
    body = await request.json()
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    try:
        content = await _generate_chat(deployment, messages)
    except RuntimeError as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": str(e),
                    "type": "bridge_error",
                }
            },
        )

    if stream:

        async def gen():
            for frame in _stream_frames(deployment, content):
                yield frame

        return StreamingResponse(gen(), media_type="text/event-stream")

    return _completion_object(deployment, content, messages)


def _fake_embedding(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    i = 0
    while len(out) < dim:
        # Expand digest deterministically
        block = hashlib.sha256(digest + i.to_bytes(4, "big")).digest()
        for b in block:
            if len(out) >= dim:
                break
            # map byte 0..255 -> [-1, 1]
            out.append((b / 127.5) - 1.0)
        i += 1
    return out


def _project_dim(vec: list[float], dim: int) -> list[float]:
    if len(vec) == dim:
        return vec
    if len(vec) > dim:
        return vec[:dim]
    return vec + [0.0] * (dim - len(vec))


async def _embed_ollama(texts: list[str]) -> list[list[float]]:
    results: list[list[float]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for t in texts:
            r = await client.post(
                f"{OLLAMA_BASE}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": t},
            )
            r.raise_for_status()
            emb = r.json()["embedding"]
            results.append(_project_dim(list(emb), EMBED_DIM))
    return results


async def _generate_embeddings(texts: list[str]) -> list[list[float]]:
    backend = EMB_BACKEND
    try:
        if backend == "fake":
            return [_fake_embedding(t, EMBED_DIM) for t in texts]
        if backend == "ollama":
            return await _embed_ollama(texts)
        raise RuntimeError(f"unknown EMB_BACKEND={backend!r}")
    except Exception as e:
        raise RuntimeError(f"embeddings backend ({backend}) failed: {e}") from e


@app.post("/openai/deployments/{deployment}/embeddings")
async def embeddings(deployment: str, request: Request) -> Any:
    body = await request.json()
    raw = body.get("input", "")
    if isinstance(raw, str):
        texts = [raw]
    elif isinstance(raw, list):
        texts = [str(x) for x in raw]
    else:
        texts = [str(raw)]

    try:
        vectors = await _generate_embeddings(texts)
    except RuntimeError as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": str(e),
                    "type": "bridge_error",
                }
            },
        )

    data = [
        {"object": "embedding", "index": i, "embedding": v}
        for i, v in enumerate(vectors)
    ]
    total = sum(max(1, len(t.split())) for t in texts)
    return {
        "object": "list",
        "model": deployment,
        "data": data,
        "usage": {"prompt_tokens": total, "total_tokens": total},
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible surface (plain OpenAI SDK, Groq, and other
# OpenAI-shaped providers). Same backends as the Azure shape above; the
# request body's "model" plays the role of the Azure deployment name.
#   /v1/...         -> OpenAI (base_url=http://127.0.0.1:8090/v1)
#   /openai/v1/...  -> Groq   (base_url=http://127.0.0.1:8090/openai/v1)
# Tool/function definitions in the request are accepted and ignored; the
# fake backend always answers with plain assistant text.
# ---------------------------------------------------------------------------


async def _model_from_body(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return str(body.get("model") or "default")


@app.post("/v1/chat/completions")
@app.post("/openai/v1/chat/completions")
async def openai_chat_completions(request: Request) -> Any:
    return await chat_completions(await _model_from_body(request), request)


@app.post("/v1/embeddings")
@app.post("/openai/v1/embeddings")
async def openai_embeddings(request: Request) -> Any:
    return await embeddings(await _model_from_body(request), request)


@app.get("/v1/models")
@app.get("/openai/v1/models")
def openai_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": "locadev-fake", "object": "model", "owned_by": "locadev"}],
    }
