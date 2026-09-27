"""OpenAI-compatible (non-Azure) bridge routes: plain OpenAI SDK + Groq path shape."""

import pytest

from openai import OpenAI

from conftest import BRIDGE, require_port


def test_bridge_openai_v1_chat_and_embeddings():
    require_port(8090, "Bridge")
    client = OpenAI(base_url=f"{BRIDGE}/v1", api_key="not-used")
    chat = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "openai shape smoke"}],
    )
    assert chat.choices[0].message.content
    emb = client.embeddings.create(model="text-embedding-3-small", input="smoke")
    assert len(emb.data[0].embedding) == 1536


def test_bridge_groq_shape_chat():
    require_port(8090, "Bridge")
    client = OpenAI(base_url=f"{BRIDGE}/openai/v1", api_key="not-used")
    chat = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "groq shape smoke"}],
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {"type": "object", "properties": {}}}}],
    )
    assert "groq shape smoke" in (chat.choices[0].message.content or "")


def test_bridge_fake_tool_directive():
    """/tool <name> {json} as the last user message -> deterministic tool call."""
    require_port(8090, "Bridge")
    client = OpenAI(base_url=f"{BRIDGE}/openai/v1", api_key="not-used")
    tools = [{
        "type": "function",
        "function": {
            "name": "send_otp",
            "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}},
        },
    }]
    chat = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": '/tool send_otp {"phone": "5551234567"}'}],
        tools=tools,
    )
    choice = chat.choices[0]
    health = __import__("httpx").get(f"{BRIDGE}/health").json()
    if health.get("chat_backend") != "fake":
        pytest.skip("/tool directive only applies to the fake chat backend")
    assert choice.finish_reason == "tool_calls"
    call = choice.message.tool_calls[0]
    assert call.function.name == "send_otp"
    assert '"5551234567"' in call.function.arguments
    # unknown tool name / no tools -> plain text
    plain = client.chat.completions.create(
        model="m", messages=[{"role": "user", "content": "/tool nope {}"}], tools=tools
    )
    assert plain.choices[0].message.content
