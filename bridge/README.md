# Model bridge

One local model endpoint that answers in each provider's wire format, so client code only swaps a base URL. Backends: `fake` (default, deterministic), `ollama`, `claude-cli` (host only).

| Shape | Route | Client wiring |
|---|---|---|
| Azure OpenAI / Foundry | `POST /openai/deployments/{name}/chat/completions`, `/embeddings` | `AzureOpenAI(azure_endpoint="http://127.0.0.1:8090")` |
| OpenAI chat + embeddings | `POST /v1/chat/completions`, `/v1/embeddings` | `OpenAI(base_url="http://127.0.0.1:8090/v1")` |
| OpenAI Responses API | `POST /v1/responses`, `/openai/v1/responses` (stream or not) | same `base_url` |
| Groq | `POST /openai/v1/chat/completions` | `base_url="http://127.0.0.1:8090/openai/v1"` |
| AWS Bedrock Converse | `POST /model/{modelId}/converse` | `boto3.client("bedrock-runtime", endpoint_url="http://127.0.0.1:8090")` |
| Gemini (Vertex / Agent Platform) | `POST /{v1,v1beta1}/projects/{p}/locations/{l}/publishers/google/models/{m}:generateContent`, `:streamGenerateContent` | `genai.Client(vertexai=True, ..., http_options=HttpOptions(base_url="http://127.0.0.1:8090/"))` |
| Gemini API | `POST /{v1,v1beta}/models/{m}:generateContent`, `:streamGenerateContent` | `genai.Client(..., http_options=...)` |
| Health | `GET /health` | |

API keys are ignored, any `api-version` works, and deployment or model names pass through.

Tool calls work on every shape: OpenAI `tool_calls`, Bedrock `toolUse`, Gemini `functionCall`, Responses `function_call` items.

Not emulated: Bedrock `ConverseStream` and `InvokeModel` (these use AWS event-stream framing).

## Deterministic tool calls (fake backend)

With `CHAT_BACKEND=fake`, a last user message of the form `/tool <name> {json args}` returns a call to that tool, as long as the request offers it. Anything else gets the echo reply `FAKE_FOUNDRY[<model>]: <last user text>`.

## Scenarios and the event log

When profile `agents` is up, every call on any shape works like this:

1. It asks the cloud-agents hub for a matching **model-layer scenario**: canned or scripted text, a tool call, or an error such as `429`. The match order is scenario, then the `/tool` directive, then the backend.
2. It records itself in the hub's event log: model, session, prompt, result and latency.

Set `LOCADEV_HUB_URL` to control this. It defaults to `http://cloud-agents:8103` in compose. When the hub is down, the bridge behaves exactly as before; a failed lookup backs off for `HUB_BACKOFF_S`.

To tie model calls to one session in the event log, send an `x-locadev-session: <id>` header.

See [`../cloud_agents/README.md`](../cloud_agents/README.md#scenarios-canned-and-scripted-responses) for the scenario format.
