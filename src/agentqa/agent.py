"""The agent loop: model → tool calls → results → model, until the task is done.

This is a manual loop on purpose (learning guide 03). The eval harness has to swap the real
model for recorded and scripted ones (`providers.py`), cap the number of steps, and keep
every message for scoring. Owning the loop makes each of those one line.

Rules the loop follows, from the Claude API tool-use reference:
- the assistant's content is appended **unchanged** (thinking and fallback blocks included);
  the history is append-only, never edited;
- every `tool_use` block gets exactly one `tool_result`, and all results of one turn go back
  together in a single user message;
- `stop_reason` is checked before content is used: `refusal` and `max_tokens` end the run with
  a named outcome, never a crash or a half-executed turn.

The loop itself never decides what is safe. Tools and the policy layer do (tools.py, policy.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from importlib import resources

from . import tools as toolbox
from .providers import Provider
from .tools import ToolLog
from .world import World

DEFAULT_PROMPT = "system_v1"
DEFAULT_MAX_STEPS = 12   # model turns per task; a runaway loop is a failure, not a long run


def load_prompt(version: str = DEFAULT_PROMPT) -> str:
    return resources.files("agentqa.prompts").joinpath(f"{version}.md").read_text()


class Outcome(str, Enum):
    COMPLETED = "completed"      # the model ended its turn with an answer
    REFUSED = "refused"          # stop_reason refusal (after any server-side fallback)
    TRUNCATED = "truncated"      # stop_reason max_tokens
    STEP_LIMIT = "step_limit"    # still calling tools after max_steps turns
    ERROR = "error"              # an unexpected stop_reason or provider error


@dataclass
class Step:
    """One model turn, as recorded for evals and traces."""

    stop_reason: str | None
    served_by: str | None          # the model that actually answered (fallbacks can change it)
    fallback: bool                 # a server-side fallback served this turn
    input_tokens: int
    output_tokens: int
    tool_calls: list[str]


@dataclass
class Run:
    task: str
    prompt_version: str
    provider: str
    outcome: Outcome
    final_text: str
    messages: list[dict]
    steps: list[Step] = field(default_factory=list)
    log: ToolLog = field(default_factory=ToolLog)
    error: str | None = None

    @property
    def input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @property
    def any_fallback(self) -> bool:
        return any(s.fallback for s in self.steps)


def _final_text(content: list[dict]) -> str:
    return "\n".join(b["text"] for b in content if b.get("type") == "text").strip()


def _was_fallback(response: dict) -> bool:
    iterations = (response.get("usage") or {}).get("iterations") or []
    return any(i.get("type") == "fallback_message" for i in iterations) or any(
        b.get("type") == "fallback" for b in response.get("content", []))


class Agent:
    def __init__(self, provider: Provider, *, prompt_version: str = DEFAULT_PROMPT,
                 max_steps: int = DEFAULT_MAX_STEPS):
        self.provider = provider
        self.prompt_version = prompt_version
        self.system = load_prompt(prompt_version)
        self.max_steps = max_steps
        self.tool_definitions = toolbox.definitions()

    def run(self, task: str, world: World) -> Run:
        messages: list[dict] = [{"role": "user", "content": task}]
        run = Run(task=task, prompt_version=self.prompt_version, provider=self.provider.name,
                  outcome=Outcome.ERROR, final_text="", messages=messages)
        for _ in range(self.max_steps):
            try:
                response = self.provider.create(system=self.system, messages=messages,
                                                tools=self.tool_definitions)
            except Exception as exc:  # recorded as an outcome; evals count it, nothing crashes
                run.outcome, run.error = Outcome.ERROR, f"{type(exc).__name__}: {exc}"
                return run
            content = response.get("content", [])
            calls = [b for b in content if b.get("type") == "tool_use"]
            usage = response.get("usage") or {}
            run.steps.append(Step(
                stop_reason=response.get("stop_reason"), served_by=response.get("model"),
                fallback=_was_fallback(response), input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0), tool_calls=[b["name"] for b in calls],
            ))
            messages.append({"role": "assistant", "content": content})  # unchanged, append-only
            stop = response.get("stop_reason")

            if stop == "end_turn":
                run.outcome, run.final_text = Outcome.COMPLETED, _final_text(content)
                return run
            if stop == "refusal":
                run.outcome, run.final_text = Outcome.REFUSED, _final_text(content)
                details = response.get("stop_details") or {}
                run.error = f"refusal: {details.get('category')}"
                return run
            if stop == "max_tokens":
                run.outcome = Outcome.TRUNCATED
                return run
            if stop == "pause_turn":
                continue
            if stop != "tool_use" or not calls:
                run.outcome, run.error = Outcome.ERROR, f"unexpected stop_reason {stop!r}"
                return run

            results = []
            for call in calls:
                result = toolbox.execute(world, run.log, call["name"], call.get("input"))
                block = {"type": "tool_result", "tool_use_id": call["id"], "content": result.content}
                if result.is_error:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})  # all results, one message

        run.outcome = Outcome.STEP_LIMIT
        return run
