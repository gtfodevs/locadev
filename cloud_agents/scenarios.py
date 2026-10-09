"""Scenario engine: canned and scripted responses for locadev.

A scenario file is JSON:

    {
      "name": "refund-flow",
      "scenarios": [
        {
          "id": "refund-model",
          "layer": "model",                       # "model" (bridge) or "agent" (cloud facades)
          "match": {"prompt": "(?i)refund"},      # all given keys must match
          "script": [                             # consumed one step per matching call
            {"text": "Let me look up that order."},
            {"tool_call": {"name": "lookup_order", "arguments": {"id": "A1"}}},
            {"error": {"status": 429, "type": "ThrottlingException", "message": "slow down"}}
          ],
          "then": "repeat_last"                   # after the script: repeat_last | loop | fallthrough
        },
        {
          "id": "aws-canned",
          "layer": "agent",
          "match": {"cloud": "aws", "agent": "demo"},
          "respond": {"text": "canned answer", "delay_ms": 150}
        }
      ]
    }

Match keys (all optional): cloud (aws|azure|gcp|openai), agent, model, session,
prompt (regex searched in the last user text), turn (1-based call count for this
scenario + session). A step is one of: text, tool_call, json (raw body for the
agent layer), error {status, type, message}; any step may add delay_ms and
stream (list of text chunks for streaming surfaces).

`respond` is a one-step script that never runs out (a canned reply).
"""

from __future__ import annotations

import re
import threading
from typing import Any

LAYERS = {"model", "agent"}
THEN = {"repeat_last", "loop", "fallthrough"}
STEP_KINDS = {"text", "tool_call", "json", "error"}


class ScenarioError(ValueError):
    pass


def _validate_step(sid: str, i: int, step: Any) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ScenarioError(f"{sid}: step {i} must be an object")
    kinds = STEP_KINDS & step.keys()
    if len(kinds) != 1:
        raise ScenarioError(f"{sid}: step {i} needs exactly one of {sorted(STEP_KINDS)}")
    if "tool_call" in step and not (step["tool_call"] or {}).get("name"):
        raise ScenarioError(f"{sid}: step {i} tool_call needs a name")
    if "error" in step and not isinstance(step["error"], dict):
        raise ScenarioError(f"{sid}: step {i} error must be an object")
    return step


def validate(doc: Any) -> list[dict[str, Any]]:
    """Return the normalized scenario list, or raise ScenarioError."""
    if isinstance(doc, list):
        doc = {"scenarios": doc}
    if not isinstance(doc, dict) or not isinstance(doc.get("scenarios"), list):
        raise ScenarioError('expected {"scenarios": [...]}')
    out: list[dict[str, Any]] = []
    for n, raw in enumerate(doc["scenarios"]):
        if not isinstance(raw, dict):
            raise ScenarioError(f"scenario {n} must be an object")
        sid = str(raw.get("id") or f"scenario-{n}")
        layer = raw.get("layer", "model")
        if layer not in LAYERS:
            raise ScenarioError(f"{sid}: layer must be one of {sorted(LAYERS)}")
        match = raw.get("match") or {}
        if not isinstance(match, dict):
            raise ScenarioError(f"{sid}: match must be an object")
        if "prompt" in match:
            try:
                re.compile(match["prompt"])
            except re.error as e:
                raise ScenarioError(f"{sid}: bad prompt regex: {e}") from e
        if ("script" in raw) == ("respond" in raw):
            raise ScenarioError(f"{sid}: give exactly one of script or respond")
        if "respond" in raw:
            steps = [_validate_step(sid, 0, raw["respond"])]
            then = "repeat_last"
        else:
            if not isinstance(raw["script"], list) or not raw["script"]:
                raise ScenarioError(f"{sid}: script must be a non-empty list")
            steps = [_validate_step(sid, i, s) for i, s in enumerate(raw["script"])]
            then = raw.get("then", "repeat_last")
            if then not in THEN:
                raise ScenarioError(f"{sid}: then must be one of {sorted(THEN)}")
        out.append({"id": sid, "layer": layer, "match": match, "steps": steps, "then": then})
    ids = [s["id"] for s in out]
    if len(ids) != len(set(ids)):
        raise ScenarioError("scenario ids must be unique")
    return out


class ScenarioStore:
    """Loaded scenarios plus per-(scenario, session) script cursors."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenarios: list[dict[str, Any]] = []
        self._cursor: dict[tuple[str, str], int] = {}
        self._turns: dict[tuple[str, str], int] = {}

    def load(self, doc: Any, append: bool = False) -> int:
        new = validate(doc)
        with self._lock:
            if append:
                existing = {s["id"] for s in self._scenarios}
                clash = existing & {s["id"] for s in new}
                if clash:
                    raise ScenarioError(f"duplicate scenario ids: {sorted(clash)}")
                self._scenarios.extend(new)
            else:
                self._scenarios = new
                self._cursor.clear()
                self._turns.clear()
            return len(self._scenarios)

    def clear(self) -> None:
        with self._lock:
            self._scenarios = []
            self._cursor.clear()
            self._turns.clear()

    def rewind(self) -> None:
        """Reset cursors and turn counts but keep the loaded scenarios."""
        with self._lock:
            self._cursor.clear()
            self._turns.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    **s,
                    "cursors": {
                        sess: pos for (sid, sess), pos in self._cursor.items() if sid == s["id"]
                    },
                }
                for s in self._scenarios
            ]

    def match(self, layer: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """Return {"scenario": id, "step": {...}, "index": n} or None.

        Advances the cursor of the first matching scenario. A scenario whose
        script is exhausted with then=fallthrough does not match, so later
        scenarios (or the real backend) answer instead.
        """
        session = str(ctx.get("session") or "")
        with self._lock:
            for s in self._scenarios:
                if s["layer"] != layer:
                    continue
                key = (s["id"], session)
                turn = self._turns.get(key, 0) + 1
                if not _matches(s["match"], ctx, turn):
                    continue
                pos = self._cursor.get(key, 0)
                steps = s["steps"]
                if pos >= len(steps):
                    if s["then"] == "fallthrough":
                        continue
                    pos = 0 if s["then"] == "loop" else len(steps) - 1
                self._turns[key] = turn
                self._cursor[key] = pos + 1
                return {"scenario": s["id"], "step": steps[pos], "index": pos, "turn": turn}
        return None


def _matches(m: dict[str, Any], ctx: dict[str, Any], turn: int) -> bool:
    for k in ("cloud", "agent", "model", "session"):
        if k in m and str(m[k]) != str(ctx.get(k) or ""):
            return False
    if "turn" in m and int(m["turn"]) != turn:
        return False
    if "prompt" in m and not re.search(m["prompt"], str(ctx.get("prompt") or "")):
        return False
    return True
