"""Model providers: where the agent's next message comes from.

All three return the same thing, a dict in the Messages API's JSON shape
(`{"model", "stop_reason", "stop_details", "content": [...], "usage": {...}}`), so the agent loop,
the tests and the evals can't tell them apart.

- `LiveProvider`: the real Claude API (Anthropic SDK). Used by the live-eval workflow (M8) and
  by people running evals locally with their own key. Never in CI (CLAUDE.md).
- `ReplayProvider`: plays back responses recorded from earlier runs ("cassettes"), keyed by a
  hash of the request. A request the cassette never saw is a `CassetteMiss`: the code or the
  prompt changed, and the session must be re-recorded rather than silently answered.
- `ScriptedProvider`: a function decides each response. Used for the worst-case model that
  tries every unsafe action (AGT-03), and for unit tests of the loop.

`RecordingProvider` wraps any provider and writes what it returns into a cassette.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"     # payments are correctness-sensitive work; measured against others in M9
MAX_TOKENS = 16000          # non-streaming default from the Claude API reference
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Provider(Protocol):
    name: str

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict: ...


# ---- live ------------------------------------------------------------------------------

@dataclass
class LiveProvider:
    """The real Claude API through the official SDK.

    Server-side refusal fallbacks are enabled (`fallbacks: "default"`, the API reference's
    default for this model): if the model declines, the API re-runs the turn on a fallback
    model. Every response records which model served it, so evals can flag fallback-served
    runs instead of silently mixing models (decision AGT-D3).
    """

    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    client: Any = None
    name: str = "live"

    def __post_init__(self) -> None:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic()  # credentials from the environment, never from code

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        return response.to_dict()


# ---- replay and recording -----------------------------------------------------------------

def request_key(*, model: str, system: str, messages: list[dict], tools: list[dict]) -> str:
    """A stable hash of everything that shapes the model's answer."""
    body = json.dumps({"model": model, "system": system, "messages": messages, "tools": tools},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class CassetteMiss(LookupError):
    """The cassette has no response for this request: re-record it."""


@dataclass
class Cassette:
    model: str
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        data = json.loads(path.read_text())
        return cls(model=data["model"], entries=data["entries"])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": self.model, "entries": self.entries}, indent=2, sort_keys=True) + "\n")


@dataclass
class ReplayProvider:
    cassette: Cassette
    name: str = "replay"

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        key = request_key(model=self.cassette.model, system=system, messages=messages, tools=tools)
        if key not in self.cassette.entries:
            raise CassetteMiss(f"no recorded response for request {key[:12]}: re-record the session")
        return json.loads(json.dumps(self.cassette.entries[key]))  # a copy: callers may append to it


@dataclass
class RecordingProvider:
    inner: Provider
    cassette: Cassette
    name: str = "recording"

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = self.inner.create(system=system, messages=messages, tools=tools)
        key = request_key(model=self.cassette.model, system=system, messages=messages, tools=tools)
        self.cassette.entries[key] = json.loads(json.dumps(response))
        return response


# ---- scripted -----------------------------------------------------------------------------

@dataclass
class ScriptedProvider:
    """Each call hands the conversation so far to `script`, which returns the next response."""

    script: Callable[[list[dict]], dict]
    model: str = "scripted"
    name: str = "scripted"

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = self.script(messages)
        return {"model": self.model, "stop_details": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}, **response}


def tool_use(block_id: str, name: str, **args: Any) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": args}


def text(value: str) -> dict:
    return {"type": "text", "text": value}
