# cloud-agents: managed agent runtimes, locally (profile `agents`)

The three managed agent platforms, on your desk, behind their **real public
invoke APIs**. Client code keeps using the vendor SDK and only swaps the
endpoint. The agent itself runs as a container on the **vendor's own container
contract**, the same one each vendor's local tooling uses, so the agent you test
here is the agent you deploy.

| Cloud | Public API emulated (hub :8103) | Agent container contract | Vendor's local dev tool |
|---|---|---|---|
| **AWS** Bedrock AgentCore Runtime | `POST /runtimes/{arn}/invocations` (`InvokeAgentRuntime`, session header, SSE or JSON) | `:8080` `POST /invocations`, `GET /ping` | `agentcore dev` (:8080) |
| **Azure** Foundry Agent Service | `/api/projects/{p}/agents/{name}/versions`, `/api/projects/{p}/openai/v1/conversations`, `/openai/v1/responses` with `agent_reference` | prompt agents: none (model via bridge). Hosted agents: `POST /responses` | `azd ai agent run` (:8088) |
| **GCP** Gemini Enterprise Agent Platform runtime (was Vertex AI Agent Engine; resource still `reasoningEngines`) | `/{v1,v1beta1}/projects/{p}/locations/{l}/reasoningEngines/{id}`: `:query`, `:streamQuery`, `/sessions`, `:appendEvent`, `/memories:generate`, `/memories:retrieve` | `:8080` `POST /api/reasoning_engine`, `POST /api/stream_reasoning_engine` (ndjson) | `adk api_server` (:8000) / `adk web` |

Model calls go to the **bridge** (:8090), which now also speaks **Bedrock
Converse**, **Gemini `generateContent`** and the **OpenAI Responses API**, next to
the Azure OpenAI, OpenAI and Groq chat shapes.

```bash
docker compose -p locadev --profile agents --profile slack up -d --build
curl -s http://127.0.0.1:8103/health
```

`sample-cloud-agent` (also on host :18081) is one container that speaks all
three contracts, with a real tool loop: `lookup_order` (canned data) and
`post_slack` (a real side effect on fake-slack). It is the default target, so
the stack works end to end before you bring your own agent.

## Client wiring (same SDK calls as production)

```python
# AWS: boto3
agentcore = boto3.client("bedrock-agentcore", endpoint_url="http://127.0.0.1:8103",
                         region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
resp = agentcore.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/sample-AbCdEf1234",
    runtimeSessionId=str(uuid.uuid4()),          # >= 33 chars, as in AWS
    payload=json.dumps({"prompt": "Where is order A1?"}).encode())

# Azure: azure-ai-projects + its OpenAI client
project = AIProjectClient(endpoint="http://127.0.0.1:8103/api/projects/demo", credential=cred)
project.agents.create_version(agent_name="helper",
    definition=PromptAgentDefinition(model="gpt-4.1", instructions="..."),
    enforce_https=False)                         # azure-core blocks bearer tokens over http otherwise
openai = project.get_openai_client()
conv = openai.conversations.create()
openai.responses.create(conversation=conv.id, input="hi",
    extra_body={"agent_reference": {"name": "helper", "type": "agent_reference"}})

# GCP: vertexai SDK (agent_engines) or REST
client = vertexai.Client(project="p", location="us-central1",
    credentials=google.oauth2.credentials.Credentials(token="x"),
    http_options={"base_url": "http://127.0.0.1:8103/"})
agent = client.agent_engines.get(name="projects/p/locations/us-central1/reasoningEngines/sample")
agent.query(message="hi", user_id="u1"); list(agent.stream_query(message="hi", user_id="u1"))
```

`tests/test_cloud_agents.py` runs all of the above with the real SDKs.

## Routing to your own agent

Targets are `name=url` lists; `*` is the fallback. The name is the AgentCore
runtime id (or the part before its `-suffix`), the Foundry agent name, or the
reasoning engine id.

```bash
# your agent running under the vendor's dev tool on the host
AGENTCORE_TARGETS='orders=http://host.docker.internal:8080'
FOUNDRY_TARGETS='orders=http://host.docker.internal:8088'
AGENT_RUNTIME_TARGETS='orders=http://host.docker.internal:8080'
```

Foundry: an agent created with `create_version` and `kind: "prompt"` runs on
the bridge. An agent that is only in `FOUNDRY_TARGETS` (or has `kind: "hosted"`)
is forwarded to the container's `/responses` with the conversation history as
`input`.

Point the agent's own model client at the bridge (`http://bridge:8090` inside
compose) so its model calls are scriptable and logged too.

## Scenarios: canned and scripted responses

A scenario file is JSON. It works at two layers:

- **`model`**: the bridge answers the LLM call. The real agent code runs, with
  scripted model behavior (text, tool calls, errors).
- **`agent`**: the hub answers the cloud invoke itself, in that cloud's format,
  so client apps can be tested against canned agent replies and cloud failures
  (AgentCore `ThrottlingException`, Foundry errors, Agent Platform `UNAVAILABLE`).

```json
{
  "scenarios": [
    {
      "id": "refund-escalation",
      "layer": "model",
      "match": {"prompt": "(?i)refund.*B2"},
      "script": [
        {"tool_call": {"name": "lookup_order", "arguments": {"order_id": "B2"}}},
        {"tool_call": {"name": "post_slack", "arguments": {"channel": "#support", "text": "Refund for order B2 needs a human."}}},
        {"text": "Order B2 is refund_pending; I've asked the support team to follow up."}
      ],
      "then": "loop"
    },
    {
      "id": "aws-throttle-drill",
      "layer": "agent",
      "match": {"cloud": "aws", "prompt": "(?i)^throttle me"},
      "respond": {"error": {"status": 429, "type": "ThrottlingException", "message": "Rate exceeded"}}
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `match` | All given keys must match: `cloud` (`aws`, `azure`, `gcp`, `openai`), `agent`, `model`, `session`, `prompt` (regex on the last user text), `turn` (1-based count of calls that matched the other keys, per scenario and session) |
| `respond` | Canned: the same step every time |
| `script` | Scripted: one step per matching call, with a separate cursor per session |
| `then` | After the script runs out: `repeat_last` (default), `loop`, or `fallthrough` (stop matching, so the real backend answers) |
| step | One of `text`, `stream` (list of chunks), `tool_call` `{name, arguments}`, `json` (raw body, agent layer), `error` `{status, type, message}`; optional `delay_ms` |

The first matching scenario wins. If nothing matches, the request takes the
normal path: the fake `/tool` directive or the real backend at the model layer,
and the agent container at the agent layer.

```bash
curl -X POST localhost:8103/_locadev/scenarios -d @scenarios/example.json      # replace
curl -X POST 'localhost:8103/_locadev/scenarios?append=true' -d @more.json      # add
curl localhost:8103/_locadev/scenarios                                          # loaded, with cursors
SCENARIOS_FILE=/scenarios/example.json                                          # load at startup
```

## Eval hooks

| Endpoint | Purpose |
|---|---|
| `GET /_locadev/events?since=&kind=&cloud=&agent=&session=` | Everything that happened: `invoke` (cloud call: request, response, status, latency), `model` (each LLM call through the bridge), `span` (OTel), `scenario` (which step fired), `control`, `memory` |
| `POST /_locadev/reset` | Clears events and runtime state (conversations, sessions, memories), resets the fakes in `RESET_URLS`, and rewinds scenario cursors. Body `{"scenarios": "keep"\|"clear"}` changes the last part. |
| `POST /v1/traces` (also host :4318) | OTLP/HTTP receiver (protobuf or JSON). Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://cloud-agents:8103` on the agent. |

A typical eval case: reset, load a scenario, invoke through the cloud SDK, then
assert on the reply **and** on the evidence (the fake-slack message, the tool
calls in `model` events, the spans).

## Honest limitations

| Expect | Get |
|---|---|
| IAM / SigV4, Entra ID, Google auth | Not checked. Any credentials work. |
| AgentCore Memory, Gateway, Identity, Code Interpreter, Browser; `/ws` and MCP/A2A protocols; `InvokeAgentRuntimeCommand` | Not emulated (Runtime HTTP protocol only) |
| AgentCore microVM session isolation and idle timeouts | Sessions are a header passed to one long-lived container |
| Foundry built-in tools (code interpreter, file search, Bing), evaluations, voice | Function tools only; others are passed to the bridge and ignored |
| Foundry hosted-agent platform conversation store | History is sent to the container as `input` |
| Agent Platform deploy / update / LRO polling | Operations complete immediately (`done: true`); engines come from `AGENT_RUNTIME_TARGETS` |
| Memory Bank LLM extraction and embedding search | Each user utterance becomes a memory; retrieval ranks by word overlap |
| Bedrock `ConverseStream`, `InvokeModel` | Not emulated (event-stream framing) |
