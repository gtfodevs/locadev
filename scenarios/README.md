# Scenarios

JSON files of canned and scripted responses for the cloud-agents hub (profile `agents`). This folder is mounted read-only at `/scenarios` in the hub container.

- Load one at startup with `SCENARIOS_FILE=/scenarios/<file>.json`.
- Or load one at runtime with `POST http://127.0.0.1:8103/_locadev/scenarios` (`?append=true` adds to what's loaded).

`example.json` shows all three styles:

- A **scripted** model conversation (lookup → escalate → answer).
- A **canned** AWS throttling error.
- A **canned** GCP streaming reply.

The format, match keys and step kinds are documented in [`../cloud_agents/README.md`](../cloud_agents/README.md#scenarios-canned-and-scripted-responses).

Keep scenario files for a specific app in that app's repo, and load them at test time. This folder is for generic examples.
