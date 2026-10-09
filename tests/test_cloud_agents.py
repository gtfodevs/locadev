"""Profile `agents`: managed agent runtimes (AWS / Azure / GCP) through their real SDKs.

Needs cloud-agents (:8103), sample-cloud-agent, bridge (:8090, fake backend)
and fake-slack (:8096). Each test starts from /_locadev/reset.
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

from conftest import BRIDGE, HUB, SLACK, require_port

AWS_ARN = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/sample-AbCdEf1234"
GCP_ENGINE = f"{HUB}/v1/projects/locadev/locations/us-central1/reasoningEngines/sample"


@pytest.fixture(autouse=True)
def _clean():
    require_port(8103, "cloud-agents")
    require_port(8090, "Bridge")
    if httpx.get(f"{BRIDGE}/health").json().get("chat_backend") != "fake":
        pytest.skip("cloud-agents tests expect the bridge's fake chat backend")
    httpx.post(f"{HUB}/_locadev/reset", json={"scenarios": "clear"}).raise_for_status()
    yield


def _events(**params: str) -> list[dict]:
    return httpx.get(f"{HUB}/_locadev/events", params=params).json()["events"]


def _slack_texts() -> list[str]:
    require_port(8096, "fake-slack")
    return [m.get("text") for m in httpx.get(f"{SLACK}/messages").json()["messages"]]


def _agentcore():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        endpoint_url=HUB,
        region_name="us-east-1",
        aws_access_key_id="locadev",
        aws_secret_access_key="locadev",
        config=Config(retries={"max_attempts": 1}),
    )


def _invoke_aws(prompt: str, session: str) -> dict:
    resp = _agentcore().invoke_agent_runtime(
        agentRuntimeArn=AWS_ARN,
        runtimeSessionId=session,
        payload=json.dumps({"prompt": prompt}).encode(),
    )
    assert resp["runtimeSessionId"] == session
    return json.loads(resp["response"].read())


# --- AWS: Bedrock AgentCore Runtime ------------------------------------------------


def test_agentcore_invoke_runs_tool_and_records_evidence():
    session = str(uuid.uuid4())
    body = _invoke_aws('/tool post_slack {"channel": "#support", "text": "hello from agentcore"}', session)
    assert body["status"] == "success"
    assert [c["name"] for c in body["tool_calls"]] == ["post_slack"]
    assert "hello from agentcore" in _slack_texts()

    invokes = _events(kind="invoke", cloud="aws")
    assert invokes and invokes[-1]["session"] == session and invokes[-1]["status"] == 200
    models = _events(kind="model", session=session)
    assert len(models) == 2  # tool call, then the final answer
    assert models[0]["result"]["kind"] == "tool_call"


def test_agentcore_scenario_throttle_surfaces_as_client_error():
    from botocore.exceptions import ClientError

    httpx.post(f"{HUB}/_locadev/scenarios", json={"scenarios": [{
        "id": "throttle", "layer": "agent", "match": {"cloud": "aws"},
        "respond": {"error": {"status": 429, "type": "ThrottlingException", "message": "Rate exceeded"}},
    }]}).raise_for_status()
    with pytest.raises(ClientError) as ei:
        _invoke_aws("anything", str(uuid.uuid4()))
    assert ei.value.response["Error"]["Code"] == "ThrottlingException"


def test_scripted_model_scenario_drives_the_real_agent_loop():
    with open("scenarios/example.json", encoding="utf-8") as f:
        httpx.post(f"{HUB}/_locadev/scenarios", json=json.load(f)).raise_for_status()
    session = str(uuid.uuid4())
    body = _invoke_aws("Can I get a refund on order B2?", session)
    assert [c["name"] for c in body["tool_calls"]] == ["lookup_order", "post_slack"]
    assert body["tool_calls"][0]["result"]["status"] == "refund_pending"
    assert body["response"].startswith("Order B2 is refund_pending")
    assert "Refund for order B2 needs a human." in _slack_texts()
    steps = [e["step_index"] for e in _events(kind="scenario") if e["scenario"] == "refund-escalation"]
    assert steps == [0, 1, 2]


def test_agent_spans_arrive_over_otlp():
    session = str(uuid.uuid4())
    _invoke_aws("hello spans", session)
    deadline = time.time() + 5
    spans: list[dict] = []
    while time.time() < deadline and not spans:
        spans = [s for s in _events(kind="span") if s["session"] == session]
        time.sleep(0.2)
    names = {s["name"] for s in spans}
    assert any(n.startswith("invoke_agent") for n in names)
    assert any(n.startswith("chat ") for n in names)


# --- Azure: Foundry Agent Service ----------------------------------------------------


class _FakeCredential:
    def get_token(self, *a, **k):
        from azure.core.credentials import AccessToken

        return AccessToken("locadev", int(time.time()) + 3600)

    def get_token_info(self, *a, **k):
        from azure.core.credentials import AccessTokenInfo

        return AccessTokenInfo("locadev", int(time.time()) + 3600)


def _project():
    from azure.ai.projects import AIProjectClient

    return AIProjectClient(endpoint=f"{HUB}/api/projects/locadev", credential=_FakeCredential())


def test_foundry_prompt_agent_conversation():
    from azure.ai.projects.models import PromptAgentDefinition

    project = _project()
    # azure-core refuses bearer tokens over http unless told otherwise
    agent = project.agents.create_version(
        agent_name="helper",
        definition=PromptAgentDefinition(model="gpt-4.1", instructions="Be brief."),
        enforce_https=False,
    )
    assert agent.name == "helper" and agent.version == "1"
    openai = project.get_openai_client()
    conv = openai.conversations.create(items=[{"type": "message", "role": "user", "content": "first"}])
    resp = openai.responses.create(
        conversation=conv.id,
        input="second question",
        extra_body={"agent_reference": {"name": "helper", "type": "agent_reference"}},
    )
    assert "FAKE_FOUNDRY[gpt-4.1]: second question" in resp.output_text
    items = openai.conversations.items.list(conv.id, order="asc")
    roles = [i.role for i in items.data if i.type == "message"]
    assert roles == ["user", "user", "assistant"]


def test_foundry_hosted_agent_and_streaming():
    openai = _project().get_openai_client()
    ref = {"agent_reference": {"name": "sample", "type": "agent_reference"}}
    resp = openai.responses.create(input="hosted hello", extra_body=ref)
    assert "hosted hello" in resp.output_text
    stream = openai.responses.create(input="stream hello", stream=True, extra_body=ref)
    types = [ev.type for ev in stream]
    assert types[0] == "response.created" and types[-1] == "response.completed"
    assert "response.output_text.delta" in types


# --- GCP: Gemini Enterprise Agent Platform runtime (reasoningEngines) -------------


def test_agent_runtime_query_and_stream_query():
    r = httpx.post(f"{GCP_ENGINE}:query", json={
        "class_method": "query", "input": {"message": "gcp hello", "user_id": "u1"}})
    assert r.status_code == 200 and "gcp hello" in r.json()["output"]
    with httpx.stream("POST", f"{GCP_ENGINE}:streamQuery", json={
            "classMethod": "stream_query",
            "input": {"message": '/tool lookup_order {"order_id": "A1"}', "user_id": "u1", "session_id": "s1"}}) as s:
        lines = [json.loads(line) for line in s.iter_lines() if line.strip()]
    kinds = [list(p)[0] for ev in lines for p in ev["content"]["parts"]]
    assert kinds == ["function_call", "function_response", "text"]
    assert _events(kind="invoke", cloud="gcp", session="s1")


def test_agent_runtime_canned_stream_scenario_as_sse():
    with open("scenarios/example.json", encoding="utf-8") as f:
        httpx.post(f"{HUB}/_locadev/scenarios", json=json.load(f)).raise_for_status()
    with httpx.stream("POST", f"{GCP_ENGINE}:streamQuery?alt=sse", json={
            "class_method": "stream_query", "input": {"message": "canned please", "user_id": "u1"}}) as s:
        data = [json.loads(line[6:]) for line in s.iter_lines() if line.startswith("data: ")]
    assert "".join(d["content"]["parts"][0]["text"] for d in data) == "Hello from a canned stream."


def test_agent_runtime_sessions_and_memory_bank():
    v1b = GCP_ENGINE.replace("/v1/", "/v1beta1/")
    op = httpx.post(f"{v1b}/sessions", json={"userId": "u1"}).json()
    assert op["done"] is True
    session = op["response"]["name"]
    sid = session.rsplit("/", 1)[1]
    r = httpx.post(f"{v1b}/sessions/{sid}:appendEvent", json={
        "author": "user", "invocationId": "i1", "timestamp": "2026-10-09T00:00:00Z",
        "content": {"role": "user", "parts": [{"text": "I prefer green tea in the morning"}]}})
    assert r.status_code == 200
    assert len(httpx.get(f"{v1b}/sessions/{sid}/events").json()["sessionEvents"]) == 1
    gen = httpx.post(f"{v1b}/memories:generate", json={"vertexSessionSource": {"session": session}}).json()
    assert gen["response"]["generatedMemories"][0]["action"] == "CREATED"
    got = httpx.post(f"{v1b}/memories:retrieve", json={
        "scope": {"user_id": "u1"}, "similaritySearchParams": {"searchQuery": "what tea"}}).json()
    assert got["retrievedMemories"][0]["memory"]["fact"] == "I prefer green tea in the morning"


# --- Model shapes on the bridge --------------------------------------------------------


def test_bedrock_converse_tool_use():
    import boto3

    client = boto3.client("bedrock-runtime", endpoint_url=BRIDGE, region_name="us-east-1",
                          aws_access_key_id="locadev", aws_secret_access_key="locadev")
    tools = {"tools": [{"toolSpec": {"name": "lookup_order",
                                     "inputSchema": {"json": {"type": "object"}}}}]}
    out = client.converse(modelId="anthropic.claude-sonnet-x",
                          messages=[{"role": "user", "content": [{"text": '/tool lookup_order {"order_id": "A1"}'}]}],
                          toolConfig=tools)
    assert out["stopReason"] == "tool_use"
    assert out["output"]["message"]["content"][0]["toolUse"]["input"] == {"order_id": "A1"}
    plain = client.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "hi aws"}]}])
    assert "hi aws" in plain["output"]["message"]["content"][0]["text"]


def test_gemini_generate_content_via_google_genai():
    from google import genai
    from google.genai.types import HttpOptions
    from google.oauth2.credentials import Credentials

    client = genai.Client(vertexai=True, project="locadev", location="us-central1",
                          credentials=Credentials(token="locadev"),
                          http_options=HttpOptions(base_url=f"{BRIDGE}/"))
    resp = client.models.generate_content(model="gemini-2.5-flash", contents="hi gemini")
    assert "hi gemini" in resp.text
    chunks = list(client.models.generate_content_stream(model="gemini-2.5-flash", contents="streamed"))
    assert "streamed" in "".join(c.text or "" for c in chunks)


def test_model_scenario_error_on_openai_responses():
    from openai import OpenAI, RateLimitError

    httpx.post(f"{HUB}/_locadev/scenarios", json={"scenarios": [{
        "id": "rl", "layer": "model", "match": {"cloud": "openai", "turn": 2},
        "respond": {"error": {"status": 429, "type": "rate_limit_exceeded", "message": "slow down"}},
    }]}).raise_for_status()
    client = OpenAI(base_url=f"{BRIDGE}/v1", api_key="locadev", max_retries=0)
    first = client.responses.create(model="gpt-4.1-mini", input="turn one")
    assert "turn one" in first.output_text
    with pytest.raises(RateLimitError):
        client.responses.create(model="gpt-4.1-mini", input="turn two")


# --- Hub hooks -------------------------------------------------------------------------


def test_scenario_validation_and_reset():
    bad = httpx.post(f"{HUB}/_locadev/scenarios", json={"scenarios": [{"id": "x", "script": []}]})
    assert bad.status_code == 400
    _invoke_aws('/tool post_slack {"channel": "#support", "text": "to be wiped"}', str(uuid.uuid4()))
    assert _events() and _slack_texts()
    r = httpx.post(f"{HUB}/_locadev/reset", json={}).json()
    assert r["status"] == "reset"
    assert _events() == [] and _slack_texts() == []
