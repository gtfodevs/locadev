# Sample cloud agent (profile `agents`)

One container that speaks the container contract of all three managed agent runtimes, so the cloud-agents hub can put any of them in front of it. It's the default target for the hub and the reference for writing your own.

| Runtime | Contract it implements (port 8080) |
|---|---|
| AWS Bedrock AgentCore | `GET /ping` → `{"status": "Healthy"}` · `POST /invocations` `{"prompt"}` → `{"response", "status"}` (session in `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`) |
| Azure Foundry hosted agent | `POST /responses` (Responses API body; history arrives as `input`; session in `x-locadev-session`) |
| GCP Agent Platform | `POST /api/reasoning_engine` `{"class_method", "input"}` → `{"output"}` · `POST /api/stream_reasoning_engine` → ndjson events |

The agent is a small tool loop against the bridge (`/v1/chat/completions`). It has two tools:

- `lookup_order`: canned orders `A1` (shipped) and `B2` (refund_pending).
- `post_slack`: a real side effect on fake-slack, so an eval can check what the agent *did*.

Its spans go to `OTEL_EXPORTER_OTLP_ENDPOINT` (the hub) and carry `session.id`. `POST /_reset` clears its session memory; the hub's `/_locadev/reset` calls it.

| Env | Default |
|---|---|
| `BRIDGE_URL` | `http://bridge:8090` |
| `SLACK_URL` | `http://fake-slack:8096` |
| `AGENT_MODEL` | `gpt-4.1-mini` |
| `AGENT_SYSTEM_PROMPT` | a short support-agent prompt |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://cloud-agents:8103` in compose |

With the fake backend, drive the tool path with `/tool post_slack {"channel": "#support", "text": "hi"}` as the prompt, or script it with a model-layer scenario.

To bring your own agent instead, run it under the vendor's dev tool (`agentcore dev`, `azd ai agent run`, `adk api_server`). Then point the hub at it with `AGENTCORE_TARGETS` / `FOUNDRY_TARGETS` / `AGENT_RUNTIME_TARGETS`.
