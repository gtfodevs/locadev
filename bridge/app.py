"""Model surface for locadev: Azure OpenAI / Foundry, OpenAI (chat + Responses),
Groq, Bedrock Converse and Gemini generateContent shapes.

Presents the same URL shape as Azure OpenAI so clients can swap endpoint only.
Backends: fake (default), ollama, claude-cli (chat, host-only).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
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


# --- Fake backend: deterministic tool calls -------------------------------
# With CHAT_BACKEND=fake, a request that offers `tools` and whose LAST message
# is a user message of the form
#     /tool <name> {"json": "args"}
# gets back an assistant message calling that tool (finish_reason
# "tool_calls"). Anything else, including follow-up turns after a tool result,
# gets the normal fake text reply. This lets scripted tests (Autonomo,
# Playwright, pytest) drive an app's real tool-execution path without an LLM.
_TOOL_DIRECTIVE = re.compile(r"^\s*/tool\s+([A-Za-z0-9_.-]+)\s*(\{.*\})?\s*$", re.S)


def _fake_tool_call(body: dict[str, Any]) -> dict[str, Any] | None:
    if CHAT_BACKEND != "fake":
        return None
    tools = body.get("tools") or []
    messages = body.get("messages") or []
    if not tools or not messages or messages[-1].get("role") != "user":
        return None
    m = _TOOL_DIRECTIVE.match(_last_user_text(messages[-1:]))
    if not m:
        return None
    name = m.group(1)
    offered = {
        (t.get("function") or {}).get("name")
        for t in tools
        if isinstance(t, dict)
    }
    if name not in offered:
        return None
    args = m.group(2) or "{}"
    try:
        json.loads(args)
    except ValueError:
        return None
    return {
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _tool_call_response(deployment: str, call: dict[str, Any], stream: bool) -> Any:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    if not stream:
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return "data: " + json.dumps({
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": deployment,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    frames = [
        chunk({"role": "assistant", "content": None, "tool_calls": [{"index": 0, **call}]}),
        chunk({}, finish="tool_calls"),
        "data: [DONE]\n\n",
    ]

    async def gen():
        for f in frames:
            yield f

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- locadev hub: scenarios (canned / scripted replies) + event log ---------
# When the cloud-agents hub (profile "agents") is up, every model call first
# asks it for a matching model-layer scenario step, and reports the call to
# its event log. If the hub is down the bridge behaves exactly as before; a
# failed lookup backs off for HUB_BACKOFF_S so the hot path stays fast.
HUB_URL = os.environ.get("LOCADEV_HUB_URL", "").rstrip("/")
HUB_BACKOFF_S = float(os.environ.get("HUB_BACKOFF_S", "5"))
_hub_down_until = 0.0


async def _hub_post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    global _hub_down_until
    if not HUB_URL or time.time() < _hub_down_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.post(f"{HUB_URL}{path}", json=payload)
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError):
        _hub_down_until = time.time() + HUB_BACKOFF_S
        return None


async def _complete(
    cloud: str,
    model: str,
    body: dict[str, Any],
    session: str = "",
) -> dict[str, Any]:
    """One model turn for any surface.

    body is OpenAI chat shaped ({"messages": [...], "tools": [...]}); each
    surface converts to it and formats the result back. Returns one of
      {"kind": "text", "text": str}
      {"kind": "tool_call", "call": {"id", "type": "function", "function": {"name", "arguments"}}}
      {"kind": "error", "status": int, "type": str, "message": str}
    in order: hub scenario step -> fake /tool directive -> chat backend.
    """
    started = time.time()
    messages = body.get("messages") or []
    prompt = _last_user_text(messages)
    out: dict[str, Any] | None = None
    scenario = ""
    hit = await _hub_post(
        "/_locadev/scenarios/match",
        {"layer": "model", "cloud": cloud, "model": model, "prompt": prompt, "session": session},
    )
    step = (hit or {}).get("match")
    if step:
        scenario = step["scenario"]
        s = step["step"]
        if s.get("delay_ms"):
            await asyncio.sleep(float(s["delay_ms"]) / 1000.0)
        if "error" in s:
            err = s["error"]
            out = {"kind": "error", "status": int(err.get("status", 500)),
                   "type": err.get("type", "server_error"), "message": err.get("message", "scenario error")}
        elif "tool_call" in s:
            tc = s["tool_call"]
            args = tc.get("arguments", {})
            out = {"kind": "tool_call", "call": {
                "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                "function": {"name": tc["name"],
                             "arguments": args if isinstance(args, str) else json.dumps(args)}}}
        elif "json" in s:
            out = {"kind": "text", "text": json.dumps(s["json"])}
        else:
            out = {"kind": "text", "text": "".join(s["stream"]) if "stream" in s else str(s["text"])}
    if out is None:
        call = _fake_tool_call(body)
        if call is not None:
            out = {"kind": "tool_call", "call": call}
    if out is None:
        try:
            out = {"kind": "text", "text": await _generate_chat(model, messages)}
        except RuntimeError as e:
            out = {"kind": "error", "status": 502, "type": "bridge_error", "message": str(e)}
    await _hub_post("/_locadev/events", {
        "kind": "model", "cloud": cloud, "model": model, "session": session, "prompt": prompt[:2000],
        "result": out, "scenario": scenario, "backend": CHAT_BACKEND,
        "latency_ms": round((time.time() - started) * 1000, 1),
    })
    return out


def _openai_error(out: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=out["status"],
        content={"error": {"message": out["message"], "type": out["type"], "code": out["type"]}},
    )


@app.post("/openai/deployments/{deployment}/chat/completions")
async def chat_completions(deployment: str, request: Request, cloud: str = "azure") -> Any:
    body = await request.json()
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    out = await _complete(cloud, deployment, body, request.headers.get("x-locadev-session", ""))
    if out["kind"] == "error":
        return _openai_error(out)
    if out["kind"] == "tool_call":
        return _tool_call_response(deployment, out["call"], stream)
    content = out["text"]

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
# Tool/function definitions in the request are passed through. The fake
# backend answers with plain assistant text, except for the deterministic
# "/tool <name> {json}" directive (see _tool_directive), which returns a
# tool call when <name> is one of the request's tools.
# ---------------------------------------------------------------------------


async def _model_from_body(request: Request) -> str | None:
    """Model name from a JSON object body, or None if the body is not JSON."""
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return str(body.get("model") or "default")


def _bad_json() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"message": "Request body must be a JSON object.", "type": "invalid_request_error"}},
    )


@app.post("/v1/chat/completions")
@app.post("/openai/v1/chat/completions")
async def openai_chat_completions(request: Request) -> Any:
    model = await _model_from_body(request)
    if model is None:
        return _bad_json()
    return await chat_completions(model, request, cloud="openai")


@app.post("/v1/embeddings")
@app.post("/openai/v1/embeddings")
async def openai_embeddings(request: Request) -> Any:
    model = await _model_from_body(request)
    if model is None:
        return _bad_json()
    return await embeddings(model, request)


@app.get("/v1/models")
@app.get("/openai/v1/models")
def openai_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": "locadev-fake", "object": "model", "owned_by": "locadev"}],
    }


# ---------------------------------------------------------------------------
# Bedrock Runtime Converse (AWS). boto3: client("bedrock-runtime",
# endpoint_url="http://127.0.0.1:8090").converse(modelId=..., messages=...)
# ConverseStream uses AWS event-stream framing and is not emulated.
# ---------------------------------------------------------------------------


def _converse_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = " ".join(s.get("text", "") for s in body.get("system") or [] if s.get("text"))
    if system:
        messages.append({"role": "system", "content": system})
    for m in body.get("messages") or []:
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for block in m.get("content") or []:
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                calls.append({"id": tu.get("toolUseId", ""), "type": "function",
                              "function": {"name": tu.get("name", ""),
                                           "arguments": json.dumps(tu.get("input", {}))}})
            elif "toolResult" in block:
                tr = block["toolResult"]
                parts = [c.get("text") if "text" in c else json.dumps(c.get("json"))
                         for c in tr.get("content") or []]
                messages.append({"role": "tool", "tool_call_id": tr.get("toolUseId", ""),
                                 "content": " ".join(p for p in parts if p)})
        if calls:
            messages.append({"role": "assistant", "content": " ".join(texts) or None, "tool_calls": calls})
        elif texts:
            messages.append({"role": m.get("role", "user"), "content": " ".join(texts)})
    tools = [
        {"type": "function", "function": {
            "name": t["toolSpec"]["name"],
            "description": t["toolSpec"].get("description", ""),
            "parameters": (t["toolSpec"].get("inputSchema") or {}).get("json", {}),
        }}
        for t in (body.get("toolConfig") or {}).get("tools") or []
        if "toolSpec" in t
    ]
    return {"messages": messages, "tools": tools}


@app.post("/model/{model_id:path}/converse")
async def bedrock_converse(model_id: str, request: Request) -> Any:
    started = time.time()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content={"message": "body must be JSON"},
                            headers={"x-amzn-ErrorType": "ValidationException"})
    chat = _converse_to_chat(body)
    out = await _complete("aws", model_id, chat, request.headers.get("x-locadev-session", ""))
    if out["kind"] == "error":
        etype = out["type"] if out["type"] != "bridge_error" else "ModelErrorException"
        return JSONResponse(status_code=out["status"], content={"message": out["message"]},
                            headers={"x-amzn-ErrorType": etype})
    if out["kind"] == "tool_call":
        fn = out["call"]["function"]
        content = [{"toolUse": {"toolUseId": out["call"]["id"], "name": fn["name"],
                                "input": json.loads(fn["arguments"] or "{}")}}]
        stop = "tool_use"
        text = fn["arguments"]
    else:
        content = [{"text": out["text"]}]
        stop = "end_turn"
        text = out["text"]
    usage = _usage_from_text(_flatten_messages(chat["messages"]), text)
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": usage["prompt_tokens"], "outputTokens": usage["completion_tokens"],
                  "totalTokens": usage["total_tokens"]},
        "metrics": {"latencyMs": int((time.time() - started) * 1000)},
    }


# ---------------------------------------------------------------------------
# Gemini generateContent (GCP Agent Platform / Vertex shape and the Gemini
# API shape). google-genai: genai.Client(vertexai=True, ...,
# http_options=HttpOptions(base_url="http://127.0.0.1:8090/")).
# ---------------------------------------------------------------------------


def _gemini_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    sys_parts = (body.get("systemInstruction") or body.get("system_instruction") or {}).get("parts") or []
    system = " ".join(p.get("text", "") for p in sys_parts if p.get("text"))
    if system:
        messages.append({"role": "system", "content": system})
    for n, c in enumerate(body.get("contents") or []):
        role = "assistant" if c.get("role") == "model" else "user"
        texts: list[str] = []
        calls: list[dict[str, Any]] = []
        for p in c.get("parts") or []:
            if "text" in p:
                texts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                calls.append({"id": f"fc_{n}_{fc.get('name', '')}", "type": "function",
                              "function": {"name": fc.get("name", ""),
                                           "arguments": json.dumps(fc.get("args", {}))}})
            elif "functionResponse" in p:
                fr = p["functionResponse"]
                messages.append({"role": "tool", "tool_call_id": f"fc_{n - 1}_{fr.get('name', '')}",
                                 "content": json.dumps(fr.get("response", {}))})
        if calls:
            messages.append({"role": "assistant", "content": " ".join(texts) or None, "tool_calls": calls})
        elif texts:
            messages.append({"role": role, "content": " ".join(texts)})
    tools = [
        {"type": "function", "function": {
            "name": d["name"], "description": d.get("description", ""),
            "parameters": d.get("parameters") or d.get("parametersJsonSchema") or {}}}
        for t in body.get("tools") or []
        for d in (t.get("functionDeclarations") or t.get("function_declarations") or [])
    ]
    return {"messages": messages, "tools": tools}


def _gemini_error(out: dict[str, Any]) -> JSONResponse:
    gstatus = {400: "INVALID_ARGUMENT", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
               429: "RESOURCE_EXHAUSTED", 503: "UNAVAILABLE"}.get(out["status"], out["type"].upper())
    return JSONResponse(status_code=out["status"],
                        content={"error": {"code": out["status"], "message": out["message"], "status": gstatus}})


async def _gemini(model: str, request: Request, stream: bool) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return _gemini_error({"status": 400, "type": "INVALID_ARGUMENT", "message": "body must be JSON"})
    chat = _gemini_to_chat(body)
    out = await _complete("gcp", model, chat, request.headers.get("x-locadev-session", ""))
    if out["kind"] == "error":
        return _gemini_error(out)
    if out["kind"] == "tool_call":
        fn = out["call"]["function"]
        parts = [{"functionCall": {"name": fn["name"], "args": json.loads(fn["arguments"] or "{}")}}]
        text = fn["arguments"]
    else:
        parts = [{"text": out["text"]}]
        text = out["text"]
    usage = _usage_from_text(_flatten_messages(chat["messages"]), text)
    resp = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {"promptTokenCount": usage["prompt_tokens"],
                          "candidatesTokenCount": usage["completion_tokens"],
                          "totalTokenCount": usage["total_tokens"]},
        "modelVersion": model,
        "responseId": uuid.uuid4().hex[:16],
    }
    if not stream:
        return resp
    if request.query_params.get("alt") == "sse":
        async def gen():
            yield f"data: {json.dumps(resp)}\r\n\r\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
    return [resp]


@app.post("/{ver}/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent")
async def vertex_generate(ver: str, project: str, location: str, model: str, request: Request) -> Any:
    return await _gemini(model, request, stream=False)


@app.post("/{ver}/projects/{project}/locations/{location}/publishers/google/models/{model}:streamGenerateContent")
async def vertex_stream_generate(ver: str, project: str, location: str, model: str, request: Request) -> Any:
    return await _gemini(model, request, stream=True)


@app.post("/{ver}/models/{model}:generateContent")
async def gemini_api_generate(ver: str, model: str, request: Request) -> Any:
    return await _gemini(model, request, stream=False)


@app.post("/{ver}/models/{model}:streamGenerateContent")
async def gemini_api_stream_generate(ver: str, model: str, request: Request) -> Any:
    return await _gemini(model, request, stream=True)


# ---------------------------------------------------------------------------
# OpenAI Responses API (stateless): /v1/responses, /openai/v1/responses.
# Azure's Foundry projects moved to this shape; the cloud-agents hub keeps
# conversation state and calls this for prompt agents.
# ---------------------------------------------------------------------------


def _responses_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": body["instructions"]})
    raw = body.get("input")
    items = [{"type": "message", "role": "user", "content": raw}] if isinstance(raw, str) else raw or []
    for it in items:
        kind = it.get("type", "message")
        if kind == "message":
            content = it.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            role = it.get("role", "user")
            messages.append({"role": "system" if role == "developer" else role, "content": content or ""})
        elif kind == "function_call":
            messages.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": it.get("call_id", ""), "type": "function",
                "function": {"name": it.get("name", ""), "arguments": it.get("arguments", "{}")}}]})
        elif kind == "function_call_output":
            out = it.get("output", "")
            messages.append({"role": "tool", "tool_call_id": it.get("call_id", ""),
                             "content": out if isinstance(out, str) else json.dumps(out)})
    tools = [
        {"type": "function", "function": {
            "name": t.get("name") or (t.get("function") or {}).get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("parameters") or (t.get("function") or {}).get("parameters") or {}}}
        for t in body.get("tools") or []
        if t.get("type") == "function"
    ]
    return {"messages": messages, "tools": tools}


def _responses_sse(resp: dict[str, Any]) -> list[str]:
    seq = 0
    frames: list[str] = []

    def emit(etype: str, payload: dict[str, Any]) -> None:
        nonlocal seq
        frames.append(f"event: {etype}\ndata: {json.dumps({'type': etype, 'sequence_number': seq, **payload})}\n\n")
        seq += 1

    emit("response.created", {"response": {**resp, "status": "in_progress", "output": []}})
    for idx, item in enumerate(resp["output"]):
        emit("response.output_item.added", {"output_index": idx, "item": {**item, "status": "in_progress"}})
        if item["type"] == "message":
            text = item["content"][0]["text"]
            emit("response.output_text.delta", {"item_id": item["id"], "output_index": idx,
                                                 "content_index": 0, "delta": text})
            emit("response.output_text.done", {"item_id": item["id"], "output_index": idx,
                                                "content_index": 0, "text": text})
        emit("response.output_item.done", {"output_index": idx, "item": item})
    emit("response.completed", {"response": resp})
    return frames


@app.post("/v1/responses")
@app.post("/openai/v1/responses")
async def responses_create(request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return _bad_json()
    if not isinstance(body, dict):
        return _bad_json()
    model = str(body.get("model") or "default")
    chat = _responses_to_chat(body)
    cloud = request.headers.get("x-locadev-cloud", "openai")
    out = await _complete(cloud, model, chat, request.headers.get("x-locadev-session", ""))
    if out["kind"] == "error":
        return _openai_error(out)
    if out["kind"] == "tool_call":
        fn = out["call"]["function"]
        output = [{"type": "function_call", "id": f"fc_{uuid.uuid4().hex[:24]}", "call_id": out["call"]["id"],
                   "name": fn["name"], "arguments": fn["arguments"], "status": "completed"}]
        text = fn["arguments"]
    else:
        output = [{"type": "message", "id": f"msg_{uuid.uuid4().hex[:24]}", "role": "assistant",
                   "status": "completed",
                   "content": [{"type": "output_text", "text": out["text"], "annotations": []}]}]
        text = out["text"]
    usage = _usage_from_text(_flatten_messages(chat["messages"]), text)
    resp = {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": body.get("tools") or [],
        "usage": {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
                  "total_tokens": usage["total_tokens"]},
    }
    if body.get("stream"):
        async def gen():
            for f in _responses_sse(resp):
                yield f

        return StreamingResponse(gen(), media_type="text/event-stream")
    return resp
