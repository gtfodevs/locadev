"""locadev cloud-agents hub: the three managed agent runtimes, locally.

Presents each cloud's public invoke API so client code only swaps an endpoint,
and forwards to agent containers that implement the vendor's own container
contract (the same one `agentcore dev`, `azd ai agent run` and the Agent
Platform runtime contract use):

  AWS   Bedrock AgentCore Runtime  POST /runtimes/{arn}/invocations
                                   -> container POST /invocations (:8080)
  Azure Foundry Agent Service      /api/projects/{p}/agents/...,
                                   /api/projects/{p}/openai/v1/{conversations,responses}
                                   -> prompt agents via the bridge, hosted
                                      agents -> container POST /responses (:8088)
  GCP   Agent Platform Runtime     /{v1,v1beta1}/projects/{p}/locations/{l}/reasoningEngines/...
        (formerly Vertex AI        :query / :streamQuery, sessions, memories
         Agent Engine)             -> container /api/reasoning_engine,
                                      /api/stream_reasoning_engine (:8080)

Plus the eval hooks: /_locadev/events, /_locadev/reset, /_locadev/scenarios
(canned + scripted responses, see scenarios.py) and an OTLP/HTTP receiver at
/v1/traces so agent spans land in the same event log.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import threading
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from scenarios import ScenarioError, ScenarioStore

app = FastAPI(title="locadev-cloud-agents")

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://bridge:8090").rstrip("/")
SCENARIOS_FILE = os.environ.get("SCENARIOS_FILE", "")
RESET_URLS = [u.strip() for u in os.environ.get("RESET_URLS", "").split(",") if u.strip()]
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "5000"))
CAPTURE_BYTES = int(os.environ.get("CAPTURE_BYTES", "65536"))
UPSTREAM_TIMEOUT_S = float(os.environ.get("UPSTREAM_TIMEOUT_S", "300"))


def _parse_targets(raw: str) -> dict[str, str]:
    """'name=url,other=url,*=url' -> {name: url}."""
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip().rstrip("/")
    return out


AGENTCORE_TARGETS = _parse_targets(os.environ.get("AGENTCORE_TARGETS", ""))
FOUNDRY_TARGETS = _parse_targets(os.environ.get("FOUNDRY_TARGETS", ""))
AGENT_RUNTIME_TARGETS = _parse_targets(os.environ.get("AGENT_RUNTIME_TARGETS", ""))


def _target(targets: dict[str, str], name: str) -> str | None:
    if name in targets:
        return targets[name]
    # AgentCore runtime ids look like "<name>-<10 char suffix>"
    base = name.rsplit("-", 1)[0] if "-" in name else name
    return targets.get(base) or targets.get("*")


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts or _now())) + "Z"


# ---------------------------------------------------------------------------
# Event log (the evidence Receipts and tests read back)
# ---------------------------------------------------------------------------


class EventLog:
    def __init__(self, cap: int) -> None:
        self._lock = threading.Lock()
        self._events: collections.deque[dict[str, Any]] = collections.deque(maxlen=cap)
        self._seq = 0

    def add(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": _now(), "kind": kind, **fields}
            self._events.append(ev)
            return ev

    def list(self, since: int = 0, **filters: str) -> list[dict[str, Any]]:
        with self._lock:
            evs = [e for e in self._events if e["seq"] > since]
        for k, v in filters.items():
            if v:
                evs = [e for e in evs if str(e.get(k, "")) == v]
        return evs

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    @property
    def seq(self) -> int:
        return self._seq


EVENTS = EventLog(MAX_EVENTS)
SCENARIOS = ScenarioStore()


def _clip(data: Any) -> Any:
    """Keep event payloads bounded."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        if len(data) > CAPTURE_BYTES:
            return data[:CAPTURE_BYTES] + f"...[{len(data) - CAPTURE_BYTES} more bytes]"
        try:
            return json.loads(data)
        except ValueError:
            return data
    return data


def _load_scenarios_file() -> None:
    if SCENARIOS_FILE and os.path.exists(SCENARIOS_FILE):
        with open(SCENARIOS_FILE, encoding="utf-8") as f:
            SCENARIOS.load(json.load(f))


_load_scenarios_file()


# ---------------------------------------------------------------------------
# Shared state for the three runtimes (reset by /_locadev/reset)
# ---------------------------------------------------------------------------

STATE_LOCK = threading.Lock()
FOUNDRY_AGENTS: dict[tuple[str, str], list[dict[str, Any]]] = {}  # (project, name) -> versions
CONVERSATIONS: dict[str, dict[str, Any]] = {}
GCP_SESSIONS: dict[str, dict[str, Any]] = {}  # full session name -> session
GCP_EVENTS: dict[str, list[dict[str, Any]]] = {}  # session name -> events
GCP_MEMORIES: dict[str, dict[str, Any]] = {}  # memory name -> memory


def _reset_state() -> None:
    with STATE_LOCK:
        FOUNDRY_AGENTS.clear()
        CONVERSATIONS.clear()
        GCP_SESSIONS.clear()
        GCP_EVENTS.clear()
        GCP_MEMORIES.clear()


# ---------------------------------------------------------------------------
# Hub endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "targets": {
            "agentcore": AGENTCORE_TARGETS,
            "foundry": FOUNDRY_TARGETS,
            "agent_runtime": AGENT_RUNTIME_TARGETS,
        },
        "scenarios": len(SCENARIOS.snapshot()),
        "events": EVENTS.seq,
    }


@app.get("/_locadev/events")
def list_events(
    since: int = 0,
    kind: str = "",
    cloud: str = "",
    agent: str = "",
    session: str = "",
) -> dict[str, Any]:
    evs = EVENTS.list(since, kind=kind, cloud=cloud, agent=agent, session=session)
    return {"events": evs, "last_seq": EVENTS.seq}


@app.post("/_locadev/events")
async def post_event(request: Request) -> dict[str, Any]:
    """Other locadev services (the bridge) record their calls here."""
    body = await request.json()
    kind = str(body.pop("kind", "external"))
    body.pop("seq", None)
    body.pop("ts", None)
    return {"seq": EVENTS.add(kind, **body)["seq"]}


@app.post("/_locadev/reset")
async def reset(request: Request) -> dict[str, Any]:
    """Clear events and runtime state; scenarios: rewind (default) | keep | clear."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    mode = (body or {}).get("scenarios", "rewind")
    EVENTS.clear()
    _reset_state()
    if mode == "clear":
        SCENARIOS.clear()
    elif mode == "rewind":
        SCENARIOS.rewind()
    downstream: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in RESET_URLS:
            try:
                r = await client.post(url)
                downstream[url] = r.status_code
            except httpx.HTTPError as e:
                downstream[url] = f"error: {type(e).__name__}"
    return {"status": "reset", "scenarios": mode, "downstream": downstream}


@app.get("/_locadev/scenarios")
def get_scenarios() -> dict[str, Any]:
    return {"scenarios": SCENARIOS.snapshot()}


@app.post("/_locadev/scenarios")
async def put_scenarios(request: Request, append: bool = False) -> Any:
    try:
        n = SCENARIOS.load(await request.json(), append=append)
    except (ScenarioError, ValueError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"loaded": n}


@app.delete("/_locadev/scenarios")
def clear_scenarios() -> dict[str, Any]:
    SCENARIOS.clear()
    return {"loaded": 0}


@app.post("/_locadev/scenarios/match")
async def match_scenario(request: Request) -> dict[str, Any]:
    """Used by the bridge for the model layer: {layer, cloud, model, prompt, session}."""
    ctx = await request.json()
    hit = SCENARIOS.match(ctx.get("layer", "model"), ctx)
    if hit:
        EVENTS.add("scenario", layer=ctx.get("layer", "model"), cloud=ctx.get("cloud", ""),
                   model=ctx.get("model", ""), session=ctx.get("session", ""),
                   scenario=hit["scenario"], step_index=hit["index"])
    return {"match": hit}


# ---------------------------------------------------------------------------
# OTLP/HTTP receiver (JSON or protobuf) -> "span" events
# ---------------------------------------------------------------------------


def _otlp_value(v: dict[str, Any]) -> Any:
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_otlp_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _otlp_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    return None


def _attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {a["key"]: _otlp_value(a.get("value", {})) for a in items or []}


@app.post("/v1/traces")
async def otlp_traces(request: Request) -> Response:
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if "protobuf" in ctype:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        msg = ExportTraceServiceRequest()
        msg.ParseFromString(raw)
        doc = MessageToDict(msg)
        # MessageToDict renders trace/span ids as base64; hex is what UIs show
        _ids_to_hex(doc)
    else:
        doc = json.loads(raw or b"{}")
    n = 0
    for rs in doc.get("resourceSpans", []):
        res = _attrs((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                start = int(sp.get("startTimeUnixNano", 0))
                end = int(sp.get("endTimeUnixNano", 0))
                attrs = _attrs(sp.get("attributes"))
                EVENTS.add(
                    "span",
                    service=res.get("service.name", ""),
                    name=sp.get("name", ""),
                    trace_id=sp.get("traceId", ""),
                    span_id=sp.get("spanId", ""),
                    parent_span_id=sp.get("parentSpanId", ""),
                    duration_ms=round((end - start) / 1e6, 3) if end and start else None,
                    status=(sp.get("status") or {}).get("code", ""),
                    session=str(attrs.get("session.id") or attrs.get("gen_ai.conversation.id") or ""),
                    attributes=attrs,
                )
                n += 1
    if "protobuf" in ctype:
        return Response(content=b"", media_type="application/x-protobuf")
    return JSONResponse({"partialSuccess": {}, "accepted": n})


def _ids_to_hex(doc: dict[str, Any]) -> None:
    import base64

    for rs in doc.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                for k in ("traceId", "spanId", "parentSpanId"):
                    if sp.get(k):
                        sp[k] = base64.b64decode(sp[k]).hex()


@app.post("/v1/logs")
@app.post("/v1/metrics")
async def otlp_ignored(request: Request) -> Response:
    """Accepted so exporters configured for all signals don't error."""
    await request.body()
    if "protobuf" in request.headers.get("content-type", ""):
        return Response(content=b"", media_type="application/x-protobuf")
    return JSONResponse({"partialSuccess": {}})


# ---------------------------------------------------------------------------
# Forwarding helper: stream upstream bytes through, capture them for the log
# ---------------------------------------------------------------------------


_client = httpx.AsyncClient(timeout=httpx.Timeout(UPSTREAM_TIMEOUT_S, connect=5.0))


async def _forward(
    url: str,
    body: bytes,
    headers: dict[str, str],
    on_done: Any,
    extra_headers: dict[str, str] | None = None,
    wrap_sse: bool = False,
) -> Response:
    """POST to an agent container; stream the reply back.

    on_done(status, captured_bytes, latency_ms) is called once the stream ends.
    Raises httpx.HTTPError when the container is unreachable.
    """
    started = time.monotonic()
    req = _client.build_request("POST", url, content=body, headers=headers)
    upstream = await _client.send(req, stream=True)
    captured = bytearray()

    async def gen() -> AsyncIterator[bytes]:
        try:
            buf = b""
            async for chunk in upstream.aiter_raw():
                if len(captured) < CAPTURE_BYTES:
                    captured.extend(chunk[: CAPTURE_BYTES - len(captured)])
                if not wrap_sse:
                    yield chunk
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        yield b"data: " + line + b"\n\n"
            if wrap_sse and buf.strip():
                yield b"data: " + buf + b"\n\n"
        finally:
            await upstream.aclose()
            on_done(upstream.status_code, bytes(captured), round((time.monotonic() - started) * 1000, 1))

    ctype = "text/event-stream" if wrap_sse else upstream.headers.get("content-type", "application/json")
    return StreamingResponse(
        gen(), status_code=upstream.status_code, media_type=ctype, headers=extra_headers or {}
    )


def _stream_text(chunks: list[Any], fmt: str) -> list[bytes]:
    if fmt == "sse":
        return [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    return [(json.dumps(c) + "\n").encode() for c in chunks]


async def _delay(step: dict[str, Any]) -> None:
    if step.get("delay_ms"):
        await asyncio.sleep(float(step["delay_ms"]) / 1000.0)


def _agent_scenario(cloud: str, agent: str, session: str, prompt: str) -> dict[str, Any] | None:
    hit = SCENARIOS.match("agent", {"cloud": cloud, "agent": agent, "session": session, "prompt": prompt})
    if hit:
        EVENTS.add("scenario", layer="agent", cloud=cloud, agent=agent, session=session,
                   scenario=hit["scenario"], step_index=hit["index"])
    return hit


def _step_text(step: dict[str, Any]) -> str:
    if "text" in step:
        return str(step["text"])
    if "stream" in step:
        return "".join(step["stream"])
    if "tool_call" in step:
        return json.dumps(step["tool_call"])
    return ""


# ---------------------------------------------------------------------------
# AWS: Bedrock AgentCore Runtime (InvokeAgentRuntime)
# ---------------------------------------------------------------------------

AWS_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
AWS_USER_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-User-Id"


def _aws_error(status: int, etype: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"message": message},
        headers={"x-amzn-ErrorType": etype},
    )


def _runtime_id(arn: str) -> str:
    # arn:aws:bedrock-agentcore:<region>:<acct>:runtime/<id>  or a bare id
    return arn.split("runtime/", 1)[1] if "runtime/" in arn else arn


@app.post("/runtimes/{arn:path}/invocations")
async def agentcore_invoke(arn: str, request: Request, qualifier: str = "DEFAULT") -> Response:
    agent = _runtime_id(arn)
    session = request.headers.get(AWS_SESSION_HEADER) or str(uuid.uuid4())
    if len(session) < 33:
        return _aws_error(400, "ValidationException",
                          "runtimeSessionId must be at least 33 characters")
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        payload = body.decode("utf-8", errors="replace")
    prompt = payload.get("prompt", "") if isinstance(payload, dict) else str(payload)
    base = {"cloud": "aws", "agent": agent, "session": session, "qualifier": qualifier}
    out_headers = {AWS_SESSION_HEADER: session}

    hit = _agent_scenario("aws", agent, session, str(prompt))
    if hit:
        step = hit["step"]
        await _delay(step)
        ev = {**base, "request": _clip(body), "scenario": hit["scenario"], "latency_ms": 0}
        if "error" in step:
            err = step["error"]
            EVENTS.add("invoke", **ev, status=int(err.get("status", 500)), response=err)
            return _aws_error(int(err.get("status", 500)), err.get("type", "InternalServerException"),
                              err.get("message", "scenario error"))
        if "stream" in step:
            EVENTS.add("invoke", **ev, status=200, response="".join(step["stream"]))
            frames = _stream_text(step["stream"], "sse")
            return StreamingResponse(iter(frames), media_type="text/event-stream", headers=out_headers)
        resp = step["json"] if "json" in step else {"response": _step_text(step), "status": "success"}
        EVENTS.add("invoke", **ev, status=200, response=resp)
        return JSONResponse(resp, headers=out_headers)

    target = _target(AGENTCORE_TARGETS, agent)
    if not target:
        return _aws_error(404, "ResourceNotFoundException",
                          f"No agent runtime target for {agent!r}; set AGENTCORE_TARGETS")
    fwd = {
        "content-type": request.headers.get("content-type", "application/json"),
        "accept": request.headers.get("accept", "*/*"),
        AWS_SESSION_HEADER: session,
    }
    for h in (AWS_USER_HEADER, "traceparent", "tracestate", "baggage", "authorization"):
        if request.headers.get(h):
            fwd[h] = request.headers[h]

    def done(status: int, data: bytes, latency: float) -> None:
        EVENTS.add("invoke", **base, target=target, request=_clip(body),
                   status=status, response=_clip(data), latency_ms=latency)

    try:
        resp = await _forward(f"{target}/invocations", body, fwd, done, extra_headers=out_headers)
    except httpx.HTTPError as e:
        EVENTS.add("invoke", **base, target=target, request=_clip(body), status=424,
                   response=f"container unreachable: {type(e).__name__}")
        return _aws_error(424, "RuntimeClientError", f"agent container unreachable at {target}")
    if resp.status_code >= 400:
        # AgentCore wraps container 4xx/5xx as 424 RuntimeClientError
        resp.status_code = 424
        resp.headers["x-amzn-ErrorType"] = "RuntimeClientError"
    return resp


# ---------------------------------------------------------------------------
# Azure: Foundry Agent Service (agents + conversations + responses)
# ---------------------------------------------------------------------------


def _az_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _agent_version_obj(project: str, name: str, v: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "agent.version",
        "id": f"{name}:{v['version']}",
        "name": name,
        "version": v["version"],
        "created_at": v["created_at"],
        "description": v.get("description", ""),
        "metadata": v.get("metadata", {}),
        "definition": v["definition"],
    }


def _agent_obj(project: str, name: str) -> dict[str, Any]:
    versions = FOUNDRY_AGENTS[(project, name)]
    return {
        "object": "agent",
        "id": name,
        "name": name,
        "state": "enabled",
        "versions": {"latest": _agent_version_obj(project, name, versions[-1])},
    }


@app.post("/api/projects/{project}/agents/{name}/versions")
async def foundry_create_version(project: str, name: str, request: Request) -> Any:
    body = await request.json()
    definition = body.get("definition") or {}
    if not definition.get("kind"):
        return _az_error(400, "invalid_request", "definition.kind is required")
    with STATE_LOCK:
        versions = FOUNDRY_AGENTS.setdefault((project, name), [])
        v = {
            "version": str(len(versions) + 1),
            "created_at": int(_now()),
            "definition": definition,
            "description": body.get("description", ""),
            "metadata": body.get("metadata", {}),
        }
        versions.append(v)
    EVENTS.add("control", cloud="azure", agent=name, action="create_version", version=v["version"])
    return _agent_version_obj(project, name, v)


@app.get("/api/projects/{project}/agents")
def foundry_list_agents(project: str) -> dict[str, Any]:
    data = [_agent_obj(p, n) for (p, n) in FOUNDRY_AGENTS if p == project]
    return {"object": "list", "data": data, "value": data, "has_more": False}


@app.get("/api/projects/{project}/agents/{name}")
def foundry_get_agent(project: str, name: str) -> Any:
    if (project, name) not in FOUNDRY_AGENTS:
        return _az_error(404, "not_found", f"agent {name!r} not found")
    return _agent_obj(project, name)


@app.get("/api/projects/{project}/agents/{name}/versions/{version}")
def foundry_get_version(project: str, name: str, version: str) -> Any:
    for v in FOUNDRY_AGENTS.get((project, name), []):
        if v["version"] == version:
            return _agent_version_obj(project, name, v)
    return _az_error(404, "not_found", f"agent {name!r} version {version!r} not found")


@app.delete("/api/projects/{project}/agents/{name}")
def foundry_delete_agent(project: str, name: str) -> Any:
    with STATE_LOCK:
        existed = FOUNDRY_AGENTS.pop((project, name), None) is not None
    return {"object": "agent.deleted", "name": name, "deleted": existed}


def _item_with_id(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"type": "message", "role": "user", "content": item}
    item = dict(item)
    item.setdefault("type", "message")
    prefix = {"message": "msg", "function_call": "fc", "function_call_output": "fco"}.get(item["type"], "item")
    item.setdefault("id", f"{prefix}_{uuid.uuid4().hex[:24]}")
    if item["type"] == "message":
        content = item.get("content")
        if isinstance(content, str):
            kind = "output_text" if item.get("role") == "assistant" else "input_text"
            item["content"] = [{"type": kind, "text": content}]
        item.setdefault("status", "completed")
    return item


def _conv_obj(conv: dict[str, Any]) -> dict[str, Any]:
    return {"id": conv["id"], "object": "conversation", "created_at": conv["created_at"],
            "metadata": conv["metadata"]}


@app.post("/api/projects/{project}/openai/v1/conversations")
async def foundry_create_conversation(project: str, request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    conv = {
        "id": f"conv_{uuid.uuid4().hex[:24]}",
        "created_at": int(_now()),
        "metadata": body.get("metadata") or {},
        "items": [_item_with_id(i) for i in body.get("items") or []],
    }
    with STATE_LOCK:
        CONVERSATIONS[conv["id"]] = conv
    return _conv_obj(conv)


@app.get("/api/projects/{project}/openai/v1/conversations/{cid}")
def foundry_get_conversation(project: str, cid: str) -> Any:
    conv = CONVERSATIONS.get(cid)
    return _conv_obj(conv) if conv else _az_error(404, "not_found", f"conversation {cid!r} not found")


@app.delete("/api/projects/{project}/openai/v1/conversations/{cid}")
def foundry_delete_conversation(project: str, cid: str) -> Any:
    with STATE_LOCK:
        existed = CONVERSATIONS.pop(cid, None) is not None
    return {"id": cid, "object": "conversation.deleted", "deleted": existed}


def _items_list(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "list",
        "data": items,
        "first_id": items[0]["id"] if items else None,
        "last_id": items[-1]["id"] if items else None,
        "has_more": False,
    }


@app.post("/api/projects/{project}/openai/v1/conversations/{cid}/items")
async def foundry_add_items(project: str, cid: str, request: Request) -> Any:
    conv = CONVERSATIONS.get(cid)
    if not conv:
        return _az_error(404, "not_found", f"conversation {cid!r} not found")
    body = await request.json()
    new = [_item_with_id(i) for i in body.get("items") or []]
    with STATE_LOCK:
        conv["items"].extend(new)
    return _items_list(new)


@app.get("/api/projects/{project}/openai/v1/conversations/{cid}/items")
def foundry_list_items(project: str, cid: str, order: str = "desc") -> Any:
    conv = CONVERSATIONS.get(cid)
    if not conv:
        return _az_error(404, "not_found", f"conversation {cid!r} not found")
    items = list(conv["items"])
    return _items_list(items if order == "asc" else items[::-1])


def _input_items(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [_item_with_id(raw)]
    return [_item_with_id(i) for i in raw]


def _last_user_text(items: list[dict[str, Any]]) -> str:
    for it in reversed(items):
        if it.get("type") == "message" and it.get("role") == "user":
            content = it.get("content")
            if isinstance(content, str):
                return content
            return " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
    return ""


def _response_obj(model: str, output: list[dict[str, Any]], conv_id: str | None,
                  agent: str | None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(_now()),
        "status": "completed",
        "model": model,
        "output": output,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    if conv_id:
        obj["conversation"] = {"id": conv_id}
    if agent:
        obj["agent_reference"] = {"type": "agent_reference", "name": agent}
    return obj


def _text_output(text: str) -> list[dict[str, Any]]:
    return [{
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }]


def _responses_sse(resp: dict[str, Any]) -> list[bytes]:
    """Minimal Responses streaming event sequence for a finished response."""
    seq = 0
    frames: list[bytes] = []

    def emit(etype: str, payload: dict[str, Any]) -> None:
        nonlocal seq
        frames.append(
            f"event: {etype}\ndata: {json.dumps({'type': etype, 'sequence_number': seq, **payload})}\n\n".encode()
        )
        seq += 1

    pending = {**resp, "status": "in_progress", "output": []}
    emit("response.created", {"response": pending})
    for idx, item in enumerate(resp["output"]):
        emit("response.output_item.added", {"output_index": idx, "item": {**item, "status": "in_progress"}})
        if item.get("type") == "message":
            for ci, part in enumerate(item.get("content", [])):
                text = part.get("text", "")
                emit("response.output_text.delta", {"item_id": item["id"], "output_index": idx,
                                                     "content_index": ci, "delta": text})
                emit("response.output_text.done", {"item_id": item["id"], "output_index": idx,
                                                    "content_index": ci, "text": text})
        emit("response.output_item.done", {"output_index": idx, "item": item})
    emit("response.completed", {"response": resp})
    return frames


@app.post("/api/projects/{project}/openai/v1/responses")
async def foundry_responses(project: str, request: Request) -> Response:
    return await _foundry_respond(project, None, request)


@app.post("/api/projects/{project}/agents/{name}/endpoint/protocols/openai/responses")
async def foundry_agent_endpoint_responses(project: str, name: str, request: Request) -> Response:
    return await _foundry_respond(project, name, request)


async def _foundry_respond(project: str, path_agent: str | None, request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        return _az_error(400, "invalid_request", "body must be JSON")
    ref = body.get("agent_reference") or body.get("agent") or {}
    agent = path_agent or (ref.get("name") if isinstance(ref, dict) else None)
    conv_ref = body.get("conversation")
    conv_id = conv_ref.get("id") if isinstance(conv_ref, dict) else conv_ref
    conv = CONVERSATIONS.get(conv_id) if conv_id else None
    if conv_id and conv is None:
        return _az_error(404, "not_found", f"conversation {conv_id!r} not found")
    new_items = _input_items(body.get("input"))
    history = (conv["items"] if conv else []) + new_items
    stream = bool(body.get("stream"))
    prompt = _last_user_text(history)
    session = conv_id or ""
    base = {"cloud": "azure", "agent": agent or "", "session": session}
    started = time.monotonic()

    versions = FOUNDRY_AGENTS.get((project, agent)) if agent else None
    definition = versions[-1]["definition"] if versions else None
    model = (definition or {}).get("model") or body.get("model") or "default"

    def finish(output: list[dict[str, Any]], status: int = 200, scenario: str = "") -> dict[str, Any]:
        resp = _response_obj(model, output, conv_id, agent)
        if conv is not None:
            with STATE_LOCK:
                conv["items"].extend(new_items + output)
        EVENTS.add("invoke", **base, request=_clip(json.dumps(body)), status=status, response=resp,
                   latency_ms=round((time.monotonic() - started) * 1000, 1), scenario=scenario)
        return resp

    def reply(resp: dict[str, Any]) -> Response:
        if stream:
            return StreamingResponse(iter(_responses_sse(resp)), media_type="text/event-stream")
        return JSONResponse(resp)

    hit = _agent_scenario("azure", agent or "", session, prompt)
    if hit:
        step = hit["step"]
        await _delay(step)
        if "error" in step:
            err = step["error"]
            EVENTS.add("invoke", **base, status=int(err.get("status", 500)), response=err,
                       scenario=hit["scenario"])
            return _az_error(int(err.get("status", 500)), err.get("type", "server_error"),
                             err.get("message", "scenario error"))
        if "json" in step:
            EVENTS.add("invoke", **base, status=200, response=step["json"], scenario=hit["scenario"])
            return JSONResponse(step["json"])
        if "tool_call" in step:
            tc = step["tool_call"]
            output = [{"type": "function_call", "id": f"fc_{uuid.uuid4().hex[:24]}",
                       "call_id": f"call_{uuid.uuid4().hex[:12]}", "name": tc["name"],
                       "arguments": json.dumps(tc.get("arguments", {})), "status": "completed"}]
        else:
            output = _text_output(_step_text(step))
        return reply(finish(output, scenario=hit["scenario"]))

    target = _target(FOUNDRY_TARGETS, agent) if agent else None
    hosted = definition is None or definition.get("kind") in ("hosted", "container_app")
    if agent and hosted and target:
        # Hosted agent: the container speaks the Responses API itself.
        fwd_body = {**body, "input": history}
        fwd_body.pop("conversation", None)
        fwd_body.pop("stream", None)
        try:
            # x-locadev-session lets the container tag its own model calls and spans
            r = await _client.post(f"{target}/responses", json=fwd_body,
                                   headers={"x-locadev-session": session})
        except httpx.HTTPError as e:
            EVENTS.add("invoke", **base, target=target, status=502,
                       response=f"container unreachable: {type(e).__name__}")
            return _az_error(502, "agent_unreachable", f"hosted agent container unreachable at {target}")
        if r.status_code >= 400:
            EVENTS.add("invoke", **base, target=target, status=r.status_code, response=_clip(r.content))
            return _az_error(r.status_code, "agent_error", r.text[:500])
        data = r.json()
        return reply(finish([_item_with_id(i) for i in data.get("output", [])]))

    if agent and definition is None:
        return _az_error(404, "not_found",
                         f"agent {agent!r} not found; create_version it or add it to FOUNDRY_TARGETS")

    # Prompt agent (or a plain model call): the bridge's Responses surface does the model work.
    fwd = {
        "model": model,
        "input": [{k: v for k, v in i.items() if k not in ("id", "status")} for i in history],
        "instructions": (definition or {}).get("instructions") or body.get("instructions"),
        "tools": (definition or {}).get("tools") or body.get("tools") or [],
    }
    try:
        r = await _client.post(f"{BRIDGE_URL}/openai/v1/responses", json=fwd,
                               headers={"x-locadev-cloud": "azure", "x-locadev-session": session})
    except httpx.HTTPError as e:
        return _az_error(502, "bridge_unreachable", f"bridge unreachable at {BRIDGE_URL}: {e}")
    if r.status_code >= 400:
        EVENTS.add("invoke", **base, status=r.status_code, response=_clip(r.content))
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")
    return reply(finish([_item_with_id(i) for i in r.json().get("output", [])]))


# ---------------------------------------------------------------------------
# GCP: Gemini Enterprise Agent Platform runtime (reasoningEngines)
# ---------------------------------------------------------------------------

GCP_PREFIX = "/{ver}/projects/{project}/locations/{location}/reasoningEngines"


def _gcp_error(status: int, gstatus: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": status, "message": message, "status": gstatus}})


def _engine_name(project: str, location: str, eid: str) -> str:
    return f"projects/{project}/locations/{location}/reasoningEngines/{eid}"


def _engine_obj(project: str, location: str, eid: str) -> dict[str, Any]:
    methods = [
        {"name": "query", "api_mode": "", "parameters": {"type": "object", "properties": {}}},
        {"name": "stream_query", "api_mode": "stream", "parameters": {"type": "object", "properties": {}}},
        {"name": "async_stream_query", "api_mode": "async_stream", "parameters": {"type": "object", "properties": {}}},
    ]
    return {
        "name": _engine_name(project, location, eid),
        "displayName": eid,
        "createTime": _iso(),
        "updateTime": _iso(),
        "spec": {"classMethods": methods},
    }


def _lro(name: str, response: dict[str, Any] | None = None) -> dict[str, Any]:
    op = {"name": f"{name}/operations/{uuid.uuid4().hex[:16]}", "done": True}
    if response is not None:
        op["response"] = response
    return op


@app.get(GCP_PREFIX)
def gcp_list_engines(ver: str, project: str, location: str) -> dict[str, Any]:
    return {"reasoningEngines": [_engine_obj(project, location, k)
                                 for k in AGENT_RUNTIME_TARGETS if k != "*"]}


@app.get(GCP_PREFIX + "/{eid}")
def gcp_get_engine(ver: str, project: str, location: str, eid: str) -> Any:
    if not _target(AGENT_RUNTIME_TARGETS, eid):
        return _gcp_error(404, "NOT_FOUND", f"reasoning engine {eid!r} not found; set AGENT_RUNTIME_TARGETS")
    return _engine_obj(project, location, eid)


async def _gcp_invoke(ver: str, project: str, location: str, eid: str, request: Request,
                      stream: bool) -> Response:
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return _gcp_error(400, "INVALID_ARGUMENT", "body must be JSON")
    # REST accepts both the proto JSON name and the field name
    method = payload.get("classMethod") or payload.get("class_method") or (
        "stream_query" if stream else "query")
    inp = payload.get("input") or {}
    session = str(inp.get("session_id") or inp.get("sessionId") or "")
    prompt = str(inp.get("message") or inp.get("input") or "")
    base = {"cloud": "gcp", "agent": eid, "session": session, "class_method": method}
    sse = request.query_params.get("alt") == "sse"

    hit = _agent_scenario("gcp", eid, session, prompt)
    if hit:
        step = hit["step"]
        await _delay(step)
        if "error" in step:
            err = step["error"]
            EVENTS.add("invoke", **base, status=int(err.get("status", 500)), response=err,
                       scenario=hit["scenario"])
            return _gcp_error(int(err.get("status", 500)), err.get("type", "INTERNAL"),
                              err.get("message", "scenario error"))
        if stream:
            chunks = step.get("stream") or [_step_text(step)]
            events = step.get("json") if isinstance(step.get("json"), list) else [
                {"content": {"role": "model", "parts": [{"text": c}]}, "author": eid} for c in chunks]
            EVENTS.add("invoke", **base, status=200, response=events, scenario=hit["scenario"])
            frames = _stream_text(events, "sse" if sse else "ndjson")
            return StreamingResponse(iter(frames), media_type="text/event-stream" if sse else "application/json")
        out = step["json"] if "json" in step else {"output": _step_text(step)}
        EVENTS.add("invoke", **base, status=200, response=out, scenario=hit["scenario"])
        return JSONResponse(out)

    target = _target(AGENT_RUNTIME_TARGETS, eid)
    if not target:
        return _gcp_error(404, "NOT_FOUND", f"reasoning engine {eid!r} not found; set AGENT_RUNTIME_TARGETS")
    path = "/api/stream_reasoning_engine" if stream else "/api/reasoning_engine"
    fwd_body = json.dumps({"class_method": method, "input": inp}).encode()

    def done(status: int, data: bytes, latency: float) -> None:
        EVENTS.add("invoke", **base, target=target, request=_clip(body), status=status,
                   response=_clip(data), latency_ms=latency)

    try:
        return await _forward(f"{target}{path}", fwd_body, {"content-type": "application/json"}, done,
                              wrap_sse=stream and sse)
    except httpx.HTTPError as e:
        EVENTS.add("invoke", **base, target=target, status=503,
                   response=f"container unreachable: {type(e).__name__}")
        return _gcp_error(503, "UNAVAILABLE", f"agent container unreachable at {target}")


@app.post(GCP_PREFIX + "/{eid}:query")
async def gcp_query(ver: str, project: str, location: str, eid: str, request: Request) -> Response:
    return await _gcp_invoke(ver, project, location, eid, request, stream=False)


@app.post(GCP_PREFIX + "/{eid}:streamQuery")
async def gcp_stream_query(ver: str, project: str, location: str, eid: str, request: Request) -> Response:
    return await _gcp_invoke(ver, project, location, eid, request, stream=True)


# --- Sessions ---


@app.post(GCP_PREFIX + "/{eid}/sessions")
async def gcp_create_session(ver: str, project: str, location: str, eid: str, request: Request) -> Any:
    body = await request.json()
    user = body.get("userId") or body.get("user_id")
    if not user:
        return _gcp_error(400, "INVALID_ARGUMENT", "userId is required")
    sid = request.query_params.get("sessionId") or str(uuid.uuid4().int)[:19]
    name = f"{_engine_name(project, location, eid)}/sessions/{sid}"
    sess = {
        "name": name,
        "userId": user,
        "displayName": body.get("displayName", ""),
        "sessionState": body.get("sessionState") or {},
        "createTime": _iso(),
        "updateTime": _iso(),
    }
    with STATE_LOCK:
        GCP_SESSIONS[name] = sess
        GCP_EVENTS[name] = []
    EVENTS.add("control", cloud="gcp", agent=eid, session=sid, action="create_session", user=user)
    return _lro(name, {"@type": "type.googleapis.com/google.cloud.aiplatform.v1beta1.Session", **sess})


@app.get(GCP_PREFIX + "/{eid}/sessions")
def gcp_list_sessions(ver: str, project: str, location: str, eid: str, filter: str = "") -> Any:
    prefix = _engine_name(project, location, eid) + "/sessions/"
    sessions = [s for n, s in GCP_SESSIONS.items() if n.startswith(prefix)]
    m = re.search(r'user_?[iI]d\s*=\s*"?([^"\s]+)"?', filter)
    if m:
        sessions = [s for s in sessions if s["userId"] == m.group(1)]
    return {"sessions": sessions}


@app.get(GCP_PREFIX + "/{eid}/sessions/{sid}")
def gcp_get_session(ver: str, project: str, location: str, eid: str, sid: str) -> Any:
    sess = GCP_SESSIONS.get(f"{_engine_name(project, location, eid)}/sessions/{sid}")
    return sess or _gcp_error(404, "NOT_FOUND", f"session {sid!r} not found")


@app.delete(GCP_PREFIX + "/{eid}/sessions/{sid}")
def gcp_delete_session(ver: str, project: str, location: str, eid: str, sid: str) -> Any:
    name = f"{_engine_name(project, location, eid)}/sessions/{sid}"
    with STATE_LOCK:
        GCP_SESSIONS.pop(name, None)
        GCP_EVENTS.pop(name, None)
    return _lro(name)


@app.post(GCP_PREFIX + "/{eid}/sessions/{sid}:appendEvent")
async def gcp_append_event(ver: str, project: str, location: str, eid: str, sid: str,
                           request: Request) -> Any:
    name = f"{_engine_name(project, location, eid)}/sessions/{sid}"
    if name not in GCP_SESSIONS:
        return _gcp_error(404, "NOT_FOUND", f"session {sid!r} not found")
    body = await request.json()
    ev = {"name": f"{name}/events/{uuid.uuid4().int % 10**19}", **body}
    with STATE_LOCK:
        GCP_EVENTS[name].append(ev)
        GCP_SESSIONS[name]["updateTime"] = _iso()
        delta = ((body.get("actions") or {}).get("stateDelta")) or {}
        GCP_SESSIONS[name]["sessionState"].update(delta)
    return {}


@app.get(GCP_PREFIX + "/{eid}/sessions/{sid}/events")
def gcp_list_session_events(ver: str, project: str, location: str, eid: str, sid: str) -> Any:
    name = f"{_engine_name(project, location, eid)}/sessions/{sid}"
    if name not in GCP_SESSIONS:
        return _gcp_error(404, "NOT_FOUND", f"session {sid!r} not found")
    return {"sessionEvents": GCP_EVENTS.get(name, [])}


# --- Memory Bank (approximation: facts are user utterances, retrieval is word overlap) ---


def _texts_from_events(events: list[dict[str, Any]]) -> list[str]:
    out = []
    for ev in events:
        content = ev.get("content") or {}
        if content.get("role", "user") != "user":
            continue
        text = " ".join(p.get("text", "") for p in content.get("parts", []) if p.get("text"))
        if text.strip():
            out.append(text.strip())
    return out


def _new_memory(engine: str, fact: str, scope: dict[str, Any]) -> dict[str, Any]:
    name = f"{engine}/memories/{uuid.uuid4().int % 10**19}"
    mem = {"name": name, "fact": fact, "scope": scope, "createTime": _iso(), "updateTime": _iso()}
    GCP_MEMORIES[name] = mem
    return mem


@app.post(GCP_PREFIX + "/{eid}/memories:generate")
async def gcp_generate_memories(ver: str, project: str, location: str, eid: str, request: Request) -> Any:
    body = await request.json()
    engine = _engine_name(project, location, eid)
    scope = body.get("scope") or {}
    events: list[dict[str, Any]] = []
    if body.get("directContentsSource"):
        events = body["directContentsSource"].get("events", [])
    elif body.get("vertexSessionSource"):
        sname = body["vertexSessionSource"].get("session", "")
        events = GCP_EVENTS.get(sname, [])
        if not scope and sname in GCP_SESSIONS:
            scope = {"user_id": GCP_SESSIONS[sname]["userId"]}
    elif body.get("directMemoriesSource"):
        events = [{"content": {"role": "user", "parts": [{"text": m.get("fact", "")}]}}
                  for m in body["directMemoriesSource"].get("directMemories", [])]
    with STATE_LOCK:
        existing = {(m["fact"], json.dumps(m["scope"], sort_keys=True)) for m in GCP_MEMORIES.values()}
        generated = []
        for fact in _texts_from_events(events):
            if (fact, json.dumps(scope, sort_keys=True)) in existing:
                continue
            generated.append({"memory": {"name": _new_memory(engine, fact, scope)["name"]},
                              "action": "CREATED"})
    EVENTS.add("memory", cloud="gcp", agent=eid, action="generate", scope=scope, created=len(generated))
    return _lro(engine, {"generatedMemories": generated})


@app.post(GCP_PREFIX + "/{eid}/memories")
async def gcp_create_memory(ver: str, project: str, location: str, eid: str, request: Request) -> Any:
    body = await request.json()
    with STATE_LOCK:
        mem = _new_memory(_engine_name(project, location, eid), body.get("fact", ""), body.get("scope") or {})
    return _lro(mem["name"], mem)


@app.get(GCP_PREFIX + "/{eid}/memories")
def gcp_list_memories(ver: str, project: str, location: str, eid: str) -> Any:
    prefix = _engine_name(project, location, eid) + "/memories/"
    return {"memories": [m for n, m in GCP_MEMORIES.items() if n.startswith(prefix)]}


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


@app.post(GCP_PREFIX + "/{eid}/memories:retrieve")
async def gcp_retrieve_memories(ver: str, project: str, location: str, eid: str, request: Request) -> Any:
    body = await request.json()
    prefix = _engine_name(project, location, eid) + "/memories/"
    scope = body.get("scope") or {}
    mems = [m for n, m in GCP_MEMORIES.items()
            if n.startswith(prefix) and all(m["scope"].get(k) == v for k, v in scope.items())]
    params = body.get("similaritySearchParams") or {}
    query = params.get("searchQuery") or params.get("search_query") or ""
    top_k = int(params.get("topK") or params.get("top_k") or 3)
    if query:
        q = _words(query)
        scored = []
        for m in mems:
            w = _words(m["fact"])
            overlap = len(q & w) / len(q | w) if q | w else 0.0
            scored.append((1.0 - overlap, m))
        scored.sort(key=lambda t: t[0])
        out = [{"memory": m, "distance": round(d, 4)} for d, m in scored[:top_k]]
    else:
        out = [{"memory": m} for m in mems]
    EVENTS.add("memory", cloud="gcp", agent=eid, action="retrieve", scope=scope, query=query, returned=len(out))
    return {"retrievedMemories": out}


@app.get("/")
def index() -> dict[str, Any]:
    return {
        "service": "locadev cloud-agents",
        "aws_agentcore": "POST /runtimes/{arn}/invocations",
        "azure_foundry": "/api/projects/{project}/agents/{name}/versions, "
                         "/api/projects/{project}/openai/v1/{conversations,responses}",
        "gcp_agent_runtime": "/v1beta1/projects/{p}/locations/{l}/reasoningEngines/{id}:query|:streamQuery, "
                             "/sessions, /memories",
        "eval": ["/_locadev/events", "/_locadev/reset", "/_locadev/scenarios"],
        "otlp": "POST /v1/traces",
    }
