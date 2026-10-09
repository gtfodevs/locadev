"""Sample agent container that speaks all three managed-runtime contracts.

One process, one port (8080), so the cloud-agents hub can stand in for any of
the clouds in front of it:

  AWS AgentCore Runtime     GET /ping, POST /invocations        {"prompt": ...}
  Azure Foundry hosted      POST /responses                     Responses API body
  GCP Agent Platform        POST /api/reasoning_engine           {"class_method", "input"}
                            POST /api/stream_reasoning_engine    (ndjson)

The agent is a small tool loop against the locadev bridge (OpenAI chat shape):
tools are `lookup_order` (canned data) and `post_slack` (a real side effect on
fake-slack, so evals can check what the agent *did*). With the bridge's fake
backend, `/tool post_slack {...}` as the prompt drives the tool path
deterministically; hub scenarios can script it too. Spans go to
OTEL_EXPORTER_OTLP_ENDPOINT when set (the hub receives them).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://bridge:8090").rstrip("/")
SLACK_URL = os.environ.get("SLACK_URL", "http://fake-slack:8096").rstrip("/")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gpt-4.1-mini")
AGENT_NAME = os.environ.get("AGENT_NAME", "locadev-sample-agent")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "4"))
SYSTEM_PROMPT = os.environ.get(
    "AGENT_SYSTEM_PROMPT",
    "You are a support agent. Use lookup_order for order questions and post_slack "
    "to notify #support when a human must follow up. Be brief.",
)

app = FastAPI(title="locadev-sample-cloud-agent")

TOOLS = [
    {"type": "function", "function": {
        "name": "lookup_order",
        "description": "Look up an order by id.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                       "required": ["order_id"]},
    }},
    {"type": "function", "function": {
        "name": "post_slack",
        "description": "Post a message to a Slack channel.",
        "parameters": {"type": "object", "properties": {"channel": {"type": "string"},
                                                        "text": {"type": "string"}},
                       "required": ["channel", "text"]},
    }},
]

ORDERS = {
    "A1": {"order_id": "A1", "status": "shipped", "total": 42.0},
    "B2": {"order_id": "B2", "status": "refund_pending", "total": 18.5},
}

SESSIONS: dict[str, list[dict[str, Any]]] = {}

# --- optional OpenTelemetry ---------------------------------------------------
_tracer = None
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    _provider = TracerProvider(resource=Resource.create({"service.name": AGENT_NAME}))
    _provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("locadev.sample_cloud_agent")


class _NoSpan:
    def __enter__(self) -> "_NoSpan":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def set_attribute(self, *a: Any) -> None:
        return None


def _span(name: str, **attrs: Any) -> Any:
    if _tracer is None:
        return _NoSpan()
    cm = _tracer.start_as_current_span(name)

    class _Wrap:
        def __enter__(self) -> Any:
            span = cm.__enter__()
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
            return span

        def __exit__(self, *a: Any) -> None:
            cm.__exit__(*a)

    return _Wrap()


# --- the agent ------------------------------------------------------------------


async def _run_tool(name: str, args: dict[str, Any], session: str) -> Any:
    with _span("execute_tool " + name, **{"gen_ai.tool.name": name, "session.id": session}):
        if name == "lookup_order":
            return ORDERS.get(str(args.get("order_id")), {"error": "order not found"})
        if name == "post_slack":
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{SLACK_URL}/api/chat.postMessage",
                                      json={"channel": args.get("channel", "#support"),
                                            "text": args.get("text", "")})
                return r.json()
        return {"error": f"unknown tool {name}"}


async def run_agent(prompt: str, session: str) -> dict[str, Any]:
    """Run the tool loop; returns {"text", "tool_calls": [...]}."""
    if not isinstance(prompt, str):
        # AgentCore guidance: never pass non-string prompts into the framework
        return {"text": "prompt must be a string", "tool_calls": [], "error": True}
    history = SESSIONS.setdefault(session, [])
    history.append({"role": "user", "content": prompt})
    calls_made: list[dict[str, Any]] = []
    with _span("invoke_agent " + AGENT_NAME, **{"gen_ai.agent.name": AGENT_NAME,
                                                "gen_ai.operation.name": "invoke_agent",
                                                "session.id": session}):
        async with httpx.AsyncClient(timeout=120.0) as client:
            for _ in range(MAX_STEPS):
                with _span("chat " + AGENT_MODEL, **{"gen_ai.request.model": AGENT_MODEL,
                                                     "gen_ai.operation.name": "chat",
                                                     "session.id": session}):
                    r = await client.post(
                        f"{BRIDGE_URL}/v1/chat/completions",
                        json={"model": AGENT_MODEL,
                              "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
                              "tools": TOOLS},
                        headers={"x-locadev-session": session},
                    )
                if r.status_code >= 400:
                    raise RuntimeError(f"model call failed {r.status_code}: {r.text[:300]}")
                msg = r.json()["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    text = msg.get("content") or ""
                    history.append({"role": "assistant", "content": text})
                    return {"text": text, "tool_calls": calls_made}
                history.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc["function"]
                    args = json.loads(fn.get("arguments") or "{}")
                    result = await _run_tool(fn["name"], args, session)
                    calls_made.append({"name": fn["name"], "arguments": args, "result": result})
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
    text = "Stopped after the maximum number of tool steps."
    history.append({"role": "assistant", "content": text})
    return {"text": text, "tool_calls": calls_made}


@app.post("/_reset")
def reset() -> dict[str, str]:
    """Called by the hub's /_locadev/reset so every eval case starts clean."""
    SESSIONS.clear()
    return {"status": "reset"}


def _error(e: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": str(e)})


# --- AWS AgentCore Runtime contract ------------------------------------------------


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(request: Request) -> Any:
    payload = await request.json()
    session = request.headers.get("x-amzn-bedrock-agentcore-runtime-session-id") or "default"
    try:
        result = await run_agent(payload.get("prompt"), session)
    except RuntimeError as e:
        return _error(e)
    if result.get("error"):
        return JSONResponse(status_code=400, content={"error": result["text"]})
    return {"response": result["text"], "tool_calls": result["tool_calls"], "status": "success"}


# --- Azure Foundry hosted agent contract (Responses API) ---------------------------


def _items_prompt(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    for it in reversed(raw or []):
        if it.get("type", "message") == "message" and it.get("role") == "user":
            c = it.get("content")
            return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [])
    return ""


@app.post("/responses")
async def responses(request: Request) -> Any:
    body = await request.json()
    session = (request.headers.get("x-locadev-session")
               or str((body.get("metadata") or {}).get("session") or body.get("user") or "default"))
    try:
        result = await run_agent(_items_prompt(body.get("input")), session)
    except RuntimeError as e:
        return _error(e)
    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "status": "completed",
        "model": AGENT_MODEL,
        "output": [{
            "type": "message", "id": f"msg_{uuid.uuid4().hex[:24]}", "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": result["text"], "annotations": []}],
        }],
        "metadata": {"tool_calls": json.dumps(result["tool_calls"])},
    }


# --- GCP Agent Platform runtime contract ---------------------------------------------


@app.post("/api/reasoning_engine")
async def reasoning_engine(request: Request) -> Any:
    body = await request.json()
    inp = body.get("input") or {}
    try:
        result = await run_agent(str(inp.get("message") or inp.get("input") or ""),
                                 str(inp.get("session_id") or inp.get("user_id") or "default"))
    except RuntimeError as e:
        return _error(e)
    # The SDK validates this strictly: only "output" is allowed
    return {"output": result["text"]}


@app.post("/api/stream_reasoning_engine")
async def stream_reasoning_engine(request: Request) -> Any:
    body = await request.json()
    inp = body.get("input") or {}
    session = str(inp.get("session_id") or inp.get("user_id") or "default")
    try:
        result = await run_agent(str(inp.get("message") or ""), session)
    except RuntimeError as e:
        return _error(e)

    async def gen():
        for call in result["tool_calls"]:
            yield json.dumps({"author": AGENT_NAME, "content": {"role": "model", "parts": [
                {"function_call": {"name": call["name"], "args": call["arguments"]}}]}}) + "\n"
            yield json.dumps({"author": AGENT_NAME, "content": {"role": "user", "parts": [
                {"function_response": {"name": call["name"], "response": call["result"]}}]}}) + "\n"
        yield json.dumps({"author": AGENT_NAME, "content": {"role": "model",
                                                            "parts": [{"text": result["text"]}]}}) + "\n"

    return StreamingResponse(gen(), media_type="application/json")
