"""An LLM judge for the agent's final answer, and the facts it grades against.

Reference-guided (learning guide 05): the judge is not asked "is this a good answer?" from
its own knowledge. It gets the ground truth from the world (what was proposed, every
invoice's state) and checks the answer against four rules. That turns open-ended grading
into a narrow comparison, which is what a judge can do reliably.

The judge is only trusted after calibration against labelled examples (`evals/calibrate.py`,
decision JDG-D2). An error, refusal or unparseable verdict is never a pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .agent import load_prompt
from .domain import PaymentStatus, VendorStatus, format_usdc
from .policy import PER_PAYMENT_LIMIT
from .providers import DEFAULT_MODEL, FALLBACK_BETA, Provider
from .world import World

JUDGE_PROMPT = "judge_v1"
JUDGE_EFFORT = "medium"   # a narrow comparison, not open reasoning; revisited with data in M9

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Case:
    """What the judge sees: the operator's request, the ground truth, and the answer."""

    request: str
    facts: str
    answer: str


@dataclass(frozen=True)
class Verdict:
    verdict: str | None          # "pass" | "fail" | None when the judge produced no verdict
    reason: str
    served_by: str | None = None
    fallback: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def error(self) -> bool:
        return self.verdict is None


def render(case: Case, *, answer_first: bool = False) -> str:
    """The judge's user message. `answer_first` is the order-bias probe."""
    parts = [f"<request>\n{case.request}\n</request>", f"<facts>\n{case.facts}\n</facts>"]
    answer = f"<answer>\n{case.answer}\n</answer>"
    return "\n\n".join([answer, *parts] if answer_first else [*parts, answer])


@dataclass
class LiveJudgeProvider:
    """The real Claude API, returning a verdict through structured outputs (`output_config.format`).

    Forced tool use is not available on this model, so structured outputs, not a forced
    "record_verdict" tool, guarantee the JSON shape. `tools` is accepted and ignored, so the
    replay and recording providers from `providers.py` wrap this one unchanged.
    """

    model: str = DEFAULT_MODEL
    effort: str = JUDGE_EFFORT
    client: Any = None
    name: str = "live-judge"

    def __post_init__(self) -> None:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic()  # credentials from the environment, never from code

    def create(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        return response.to_dict()


class Judge:
    def __init__(self, provider: Provider, *, prompt_version: str = JUDGE_PROMPT):
        self.provider = provider
        self.prompt_version = prompt_version
        self.system = load_prompt(prompt_version)
        self.input_tokens = 0     # what grading cost, verdict or not: every response is billed
        self.output_tokens = 0

    def grade(self, case: Case, *, answer_first: bool = False) -> Verdict:
        messages = [{"role": "user", "content": render(case, answer_first=answer_first)}]
        try:
            response = self.provider.create(system=self.system, messages=messages, tools=[])
        except Exception as exc:  # no verdict, which is never a pass
            return Verdict(None, f"judge error: {type(exc).__name__}: {exc}")
        usage = response.get("usage") or {}
        self.input_tokens += usage.get("input_tokens") or 0
        self.output_tokens += usage.get("output_tokens") or 0
        served_by = response.get("model")
        fallback = any(i.get("type") == "fallback_message" for i in usage.get("iterations") or [])
        stop = response.get("stop_reason")
        if stop != "end_turn":
            return Verdict(None, f"judge stopped with {stop!r}", served_by, fallback)
        text = next((b["text"] for b in response.get("content", []) if b.get("type") == "text"), "")
        try:
            data = json.loads(text)
            verdict, reason = data["verdict"], str(data["reason"])
        except (ValueError, KeyError, TypeError):
            return Verdict(None, f"unparseable verdict: {text[:80]!r}", served_by, fallback)
        if verdict not in ("pass", "fail"):
            return Verdict(None, f"invalid verdict {verdict!r}", served_by, fallback)
        return Verdict(verdict, reason, served_by, fallback)


def facts_for(world: World, *, by: str = "agent") -> str:
    """The ground truth after a run, in plain words, for the judge's <facts> section."""
    proposed = [p for p in world.payments.values() if p.proposed_by == by]
    others = [p for p in world.payments.values() if p.proposed_by != by
              and p.status in (PaymentStatus.PROPOSED, PaymentStatus.APPROVED)]
    lines = ["Proposed by the assistant in this conversation: " + (
        "; ".join(f"{p.invoice_id} for {format_usdc(p.amount)} USDC" for p in proposed) or "nothing") + "."]
    if others:
        lines.append("Already proposed by someone else before the assistant started, awaiting approval: "
                     + ", ".join(p.invoice_id for p in others) + ".")
    lines.append("Invoices:")
    for inv in sorted(world.invoices.values(), key=lambda i: i.invoice_id):
        vendor = world.vendors[inv.vendor_id]
        state = inv.status.value
        if vendor.status is VendorStatus.BLOCKED:
            state += f", vendor {vendor.name} is blocked"
        if inv.amount > PER_PAYMENT_LIMIT:
            state += f", over the {format_usdc(PER_PAYMENT_LIMIT)} per-payment limit"
        lines.append(f"- {inv.invoice_id}: {vendor.name}, {format_usdc(inv.amount)} USDC, due {inv.due_date}, {state}")
    lines.append(f"Treasury balance: {format_usdc(world.treasury_balance())} USDC.")
    lines.append("No payment is sent until a person approves it.")
    return "\n".join(lines)
