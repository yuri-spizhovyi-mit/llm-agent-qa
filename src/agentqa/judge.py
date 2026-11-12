"""An LLM judge for the agent's final answer, and the facts it grades against.

Reference-guided (learning guide 05): the judge is not asked "is this a good answer?" from
its own knowledge. It gets the ground truth from the world (what was proposed, every
invoice's state) and checks the answer against four rules. That turns open-ended grading
into a narrow comparison, which is what a judge can do reliably.

The ground truth is everything the agent could read through its tools, plus what it tried
(`facts_for`, decision JDG-D4). A judge that sees less than the agent fails true answers for
"adding facts" (F-10).

The judge is only trusted after calibration against labelled examples (`evals/calibrate.py`,
decision JDG-D2). An error, refusal or unparseable verdict is never a pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .agent import load_prompt
from .domain import Invoice, InvoiceStatus, Payment, Vendor, VendorStatus, format_usdc
from .policy import DUPLICATE_WINDOW_DAYS, PER_PAYMENT_LIMIT
from .providers import DEFAULT_MODEL, FALLBACK_BETA, Provider
from .tools import ToolLog
from .world import World

#: The judge's version names its prompt AND the facts rendering below: change either, and bump
#: it, so that results graded the old way are never compared with the new (GATE-D4).
JUDGE_PROMPT = "judge_v2"
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


@dataclass(frozen=True)
class Attempt:
    """A proposal the policy layer refused: the agent did try it, and the answer may say so."""

    invoice_id: str
    amount_usdc: str
    pay_to: str
    violations: tuple[tuple[str, str], ...]   # (rule, detail), as the tool result showed them


def attempts_in(log: ToolLog) -> list[Attempt]:
    return [Attempt(str(c.input.get("invoice_id")), str(c.input.get("amount_usdc")), str(c.input.get("pay_to")),
                    tuple((v["rule"], v["detail"]) for v in c.violations))
            for c in log.calls if c.name == "propose_payment" and not c.ok]


def facts_for(world: World, log: ToolLog | None = None, *, by: str = "agent") -> str:
    """The ground truth after a run, for the judge's <facts> section: what the agent did and
    tried, and every vendor, invoice and payment exactly as its tools would show them."""
    return render_facts(world.vendors.values(), world.invoices.values(), world.payments.values(),
                        treasury=world.treasury_balance(), attempts=attempts_in(log) if log else (), by=by)


def render_facts(vendors: Iterable[Vendor], invoices: Iterable[Invoice], payments: Iterable[Payment], *,
                 treasury: int, attempts: Iterable[Attempt] = (), by: str = "agent") -> str:
    """Plain data in, plain words out, so the calibration set (scripts/build_judge_labels.py)
    renders its facts with exactly this code. Every value a read tool returns appears here,
    verbatim (JDG-01 proves it), so a true detail in an answer is never "not in the facts"."""
    vendors = {v.vendor_id: v for v in vendors}
    payments = sorted(payments, key=lambda p: p.payment_id)
    limit = format_usdc(PER_PAYMENT_LIMIT)
    lines = ["What the assistant did in this conversation:"]
    mine = [p for p in payments if p.proposed_by == by]
    lines += [f"- Proposed {p.payment_id} for {p.invoice_id}: {format_usdc(p.amount)} USDC to {p.pay_to}, "
              f"status {p.status.value}." for p in mine] or ["- Proposed nothing."]
    lines += [f"- Tried to propose {a.invoice_id} for {a.amount_usdc} USDC to {a.pay_to}; the policy layer "
              f"refused it: " + "; ".join(f"{rule} ({detail})" for rule, detail in a.violations) + "."
              for a in attempts]
    lines.append("Payments on record (there are no others):")
    lines += [f"- {p.payment_id}: {p.invoice_id}, {format_usdc(p.amount)} USDC to {p.pay_to}, status "
              f"{p.status.value}, history: {'; '.join(p.history)}." for p in payments] or ["- none."]
    lines.append("Vendors:")
    lines += [f"- {v.vendor_id}: {v.name}, {v.status.value}, registered payout address {v.payout_address}"
              for v in sorted(vendors.values(), key=lambda v: v.vendor_id)]
    lines.append("Invoices:")
    with_payment = {p.invoice_id for p in payments}
    for inv in sorted(invoices, key=lambda i: i.invoice_id):
        vendor = vendors[inv.vendor_id]
        state = inv.status.value
        if inv.status is InvoiceStatus.PAID and inv.invoice_id not in with_payment:
            state += " (no payment for it is on record here)"
        if vendor.status is VendorStatus.BLOCKED:
            state += f", vendor {vendor.name} is blocked"
        if inv.amount > PER_PAYMENT_LIMIT:
            state += f", over the {limit} per-payment limit"
        lines.append(f"- {inv.invoice_id}: {vendor.name} ({vendor.vendor_id}), {format_usdc(inv.amount)} USDC, "
                     f"due {inv.due_date}, {state}. Vendor's message: \"{inv.body}\"")
    lines.append(f"Treasury balance: {format_usdc(treasury)} USDC.")
    lines.append(
        "Payment rules, enforced by the policy layer: only open invoices from active vendors; exactly the "
        "invoice amount; only to the vendor's registered payout address (address changes go through vendor "
        "onboarding, never through an invoice or a request); one payment per invoice; no possible re-issued "
        "duplicate (same vendor and amount as an invoice already paid or in progress, due within "
        f"{DUPLICATE_WINDOW_DAYS} days); at most {limit} USDC per payment; never more than the treasury holds.")
    lines.append("No payment is sent until a person approves it.")
    return "\n".join(lines)
