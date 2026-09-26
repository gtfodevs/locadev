"""OpenAI-compatible (non-Azure) bridge routes: plain OpenAI SDK + Groq path shape."""

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
