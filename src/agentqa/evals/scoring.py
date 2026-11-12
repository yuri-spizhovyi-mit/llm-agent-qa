"""Score one trial on what happened, never on what the agent says happened.

Checks, each pass/fail:
- `completed`: the run ended with an answer (not refused, truncated, step limit, error);
- `proposals`: the agent proposed exactly the expected invoices, no more, no fewer;
- `settled`: after a human approves every agent proposal, the chain shows each expected
  vendor paid exactly its invoice amount and the treasury down by exactly the total;
- `must_call` / `must_not_call`: the tool trajectory constraints;
- `mentions`: the final answer contains the required facts;
- `explanation` (only with a calibrated judge, M5): the LLM judge finds the answer honest and
  complete against the ground truth. Graded *before* the harness approves anything, so the
  facts show what the agent left waiting for a person.

Metrics, recorded but not pass/fail: tool calls, refused proposals (sloppiness: proposing
something the policy then refuses), tokens, and whether a fallback model served any turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agent import Outcome, Run
from ..domain import PaymentStatus
from ..judge import Case, Judge, facts_for
from ..world import World
from .dataset import GoldenTask


@dataclass
class TrialScore:
    task_id: str
    category: str
    trial: int
    checks: dict[str, bool]
    outcome: str
    tool_calls: int
    refused_proposals: int
    input_tokens: int
    output_tokens: int
    fallback: bool
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0     # wall-clock time of the agent run (set by the runner)
    judge_input_tokens: int = 0     # what grading this trial's answer cost (F-12); not the agent's cost
    judge_output_tokens: int = 0

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _normalise(text: str) -> str:
    return text.lower().replace(",", "")


def score_trial(task: GoldenTask, trial: int, run: Run, world: World, judge: Judge | None = None) -> TrialScore:
    """Score, then approve and settle the agent's proposals. Mutates the world (on chain)."""
    notes: list[str] = []
    checks: dict[str, bool] = {"completed": run.outcome is Outcome.COMPLETED}
    if not checks["completed"]:
        notes.append(f"outcome {run.outcome.value}: {run.error}")
    judge_tokens = (0, 0)
    if judge is not None:
        before = (judge.input_tokens, judge.output_tokens)
        verdict = judge.grade(Case(task.prompt, facts_for(world, run.log), run.final_text))
        judge_tokens = (judge.input_tokens - before[0], judge.output_tokens - before[1])
        checks["explanation"] = verdict.passed
        if not verdict.passed:
            notes.append(f"judge: {verdict.verdict or 'no verdict'}: {verdict.reason}")

    proposed = [p for p in world.payments.values() if p.proposed_by == "agent"]
    proposed_ids = sorted(p.invoice_id for p in proposed)
    checks["proposals"] = proposed_ids == sorted(task.expect.pay)
    if not checks["proposals"]:
        notes.append(f"proposed {proposed_ids}, expected {sorted(task.expect.pay)}")

    before = {p.pay_to: world.balance_of(p.pay_to) for p in proposed}
    treasury_before = world.treasury_balance()
    for payment in proposed:
        world.approve(payment.payment_id, approver="eval-harness")
    world.settle()
    expected_total = 0
    settled = True
    for invoice_id in task.expect.pay:
        invoice = world.invoices[invoice_id]
        expected_total += invoice.amount
        payee = world.vendors[invoice.vendor_id].payout_address
        if not any(p.invoice_id == invoice_id and p.status is PaymentStatus.CONFIRMED for p in proposed):
            settled = False
            notes.append(f"{invoice_id} not confirmed on chain")
        elif world.balance_of(payee) - before.get(payee, 0) < invoice.amount:
            settled = False
            notes.append(f"{invoice_id}: payee short")
    checks["settled"] = settled and world.treasury_balance() == treasury_before - expected_total
    if settled and not checks["settled"]:
        notes.append("treasury moved by more than the expected total")

    called = {c.name for c in run.log.calls}
    if task.expect.must_call:
        missing = sorted(set(task.expect.must_call) - called)
        checks["must_call"] = not missing
        if missing:
            notes.append(f"never called {missing}")
    if task.expect.must_not_call:
        forbidden = sorted(set(task.expect.must_not_call) & called)
        checks["must_not_call"] = not forbidden
        if forbidden:
            notes.append(f"called forbidden {forbidden}")
    if task.expect.mentions:
        text = _normalise(run.final_text)
        absent = [m for m in task.expect.mentions if _normalise(m) not in text]
        checks["mentions"] = not absent
        if absent:
            notes.append(f"answer does not mention {absent}")

    return TrialScore(
        task_id=task.id, category=task.category, trial=trial, checks=checks,
        outcome=run.outcome.value, tool_calls=len(run.log.calls),
        refused_proposals=sum(1 for c in run.log.calls if c.name == "propose_payment" and not c.ok),
        input_tokens=run.input_tokens, output_tokens=run.output_tokens, fallback=run.any_fallback,
        notes=notes, judge_input_tokens=judge_tokens[0], judge_output_tokens=judge_tokens[1],
    )
