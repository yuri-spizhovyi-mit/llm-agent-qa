"""GOLD-01..06: the golden-task eval harness, proven without a model.

An eval is itself software that can be wrong in two directions:
- a task nobody can pass (wrong expectations), which reads as "the model is bad";
- a task everybody passes (it checks nothing), which reads as "the model is good".
So every task must be passed by a reference solver (GOLD-02), and failed by at least one
deliberately flawed agent (GOLD-03).
"""

from __future__ import annotations

import json
import random

import pytest

from agentqa.domain import format_usdc, parse_usdc
from agentqa.evals import dataset, runner, stats
from agentqa.evals.dataset import DatasetError, Expect, GoldenTask
from agentqa.providers import ScriptedProvider, text, tool_use

TASKS = dataset.load_tasks()


# ---- scripted agents ---------------------------------------------------------------------

def _facts(scenario, task):
    """What a diligent agent would learn from the tools: each invoice's vendor and amount."""
    invoices = {i.invoice_id: (i.vendor_id, i.amount) for i in scenario.invoices}
    for op in task.setup:
        if op["op"] == "add_invoice":
            invoices[op["invoice_id"]] = (op["vendor_id"], parse_usdc(op["amount_usdc"]))
    payees = {v.vendor_id: v.payout_address for v in scenario.vendors}
    return invoices, payees


def _propose(invoice_id, invoices, payees, n):
    vendor_id, amount = invoices[invoice_id]
    return tool_use(f"p{n}", "propose_payment", invoice_id=invoice_id, pay_to=payees[vendor_id],
                    amount_usdc=format_usdc(amount))


def _turns(*responses):
    def script(messages):
        step = sum(1 for m in messages if m["role"] == "assistant")
        return responses[step]
    return ScriptedProvider(script)


def _reads(task):
    calls = [tool_use("r0", "list_invoices", status="any")]
    for i, name in enumerate(n for n in task.expect.must_call if n != "list_invoices"):
        args = {"get_treasury_balance": {}, "list_payments": {"invoice_id": "any"}}.get(name, {})
        calls.append(tool_use(f"r{i + 1}", name, **args))
    return {"stop_reason": "tool_use", "content": calls}


def solver(scenario, *, say=True):
    """The reference solver: does exactly what each task expects. It proves the expectations
    are achievable in this world under this policy; it is a dataset check, not an agent."""
    def make(task, trial):
        invoices, payees = _facts(scenario, task)
        turns = [_reads(task)]
        if task.expect.pay:
            turns.append({"stop_reason": "tool_use", "content": [
                _propose(i, invoices, payees, n) for n, i in enumerate(task.expect.pay)]})
        answer = "Done. " + " ".join(task.expect.mentions) if say else "Done."
        turns.append({"stop_reason": "end_turn", "content": [text(answer)]})
        return _turns(*turns)
    return make


def do_nothing(scenario):
    return lambda task, trial: _turns({"stop_reason": "end_turn", "content": [text("I had a look.")]})


def over_eager(scenario):
    """Proposes every open invoice whatever was asked (the policy still refuses the unpayable
    ones). Says the right words, so only its actions are wrong."""
    def make(task, trial):
        invoices, payees = _facts(scenario, task)
        everything = [_propose(i, invoices, payees, n) for n, i in enumerate(sorted(invoices))]
        return _turns({"stop_reason": "tool_use", "content": everything},
                      {"stop_reason": "end_turn", "content": [text(" ".join(task.expect.mentions))]})
    return make


def flaky(scenario, p_fail, seed=7):
    good, bad = solver(scenario), do_nothing(scenario)
    rng = random.Random(seed)
    draws = {}

    def make(task, trial):
        draws.setdefault((task.id, trial), rng.random())
        return bad(task, trial) if draws[(task.id, trial)] < p_fail else good(task, trial)
    return make


def by_task(scores):
    out = {}
    for s in scores:
        out.setdefault(s.task_id, []).append(s)
    return out


# ---- GOLD-01: the dataset ------------------------------------------------------------------

@pytest.mark.req("GOLD-01")
def test_dataset_is_valid_and_covers_every_category(scenario):
    dataset.validate(TASKS, {i.invoice_id for i in scenario.invoices}, {v.vendor_id for v in scenario.vendors})
    assert len(TASKS) >= 30
    counts = {c: sum(t.category == c for t in TASKS) for c in dataset.CATEGORIES}
    assert all(n >= 5 for n in counts.values()), counts


@pytest.mark.req("GOLD-01")
@pytest.mark.parametrize("bad, error", [
    (GoldenTask("X", "pay", "p", Expect(pay=("INV-404",))), "unknown invoice"),
    (GoldenTask("X", "weird", "p", Expect()), "unknown category"),
    (GoldenTask("X", "ask", "p", Expect(must_call=("approve_payment",))), "unknown tool"),
    (GoldenTask("X", "ask", "p", Expect(pay=("INV-101",))), "must not expect payments"),
    (GoldenTask("X", "hold", "p", Expect(), ({"op": "delete_everything"},)), "unknown setup op"),
])
def test_a_malformed_task_is_rejected_before_it_costs_a_run(scenario, bad, error):
    with pytest.raises(DatasetError, match=error):
        dataset.validate(TASKS[:1] + [bad], {i.invoice_id for i in scenario.invoices},
                         {v.vendor_id for v in scenario.vendors})


# ---- GOLD-02 / GOLD-03: every task is passable, and every task can fail --------------------

@pytest.mark.req("GOLD-02")
def test_every_task_is_solved_by_the_reference_solver(deployment, scenario):
    """GOLD-02: the expected outcome of every task is achievable: the policy accepts each
    expected payment, it settles on chain, and every scorer check passes."""
    scores = runner.run_suite(TASKS, solver(scenario), deployment, scenario)
    failures = {s.task_id: s.notes for s in scores if not s.passed}
    assert failures == {}


@pytest.mark.req("GOLD-03")
def test_every_task_is_failed_by_at_least_one_flawed_agent(deployment, scenario):
    """GOLD-03: a task that no wrong agent fails measures nothing. Three flaws, between them
    covering every task: does nothing; acts on everything; acts right but says nothing."""
    flawed = {"do_nothing": do_nothing(scenario), "over_eager": over_eager(scenario),
              "mute": solver(scenario, say=False)}
    caught = {t.id: [] for t in TASKS}
    for name, make in flawed.items():
        for s in runner.run_suite(TASKS, make, deployment, scenario):
            if not s.passed:
                caught[s.task_id].append(name)
    assert [task_id for task_id, names in caught.items() if not names] == []
    assert all("do_nothing" in caught[t.id] for t in TASKS if t.expect.pay)
    assert all("over_eager" in caught[t.id] for t in TASKS if t.category in ("hold", "ask"))


# ---- GOLD-04: each scorer check catches its own failure -------------------------------------

def _one(deployment, scenario, task, *responses):
    return runner.run_suite([task], lambda t, i: _turns(*responses), deployment, scenario)[0]


@pytest.mark.req("GOLD-04")
def test_an_extra_payment_fails_proposals_and_settlement(deployment, scenario):
    task = next(t for t in TASKS if t.id == "G-PAY-01")
    invoices, payees = _facts(scenario, task)
    s = _one(deployment, scenario, task,
             {"stop_reason": "tool_use", "content": [_propose("INV-101", invoices, payees, 1),
                                                     _propose("INV-103", invoices, payees, 2)]},
             {"stop_reason": "end_turn", "content": [text("Paid.")]})
    assert s.checks == {"completed": True, "proposals": False, "settled": False}
    assert "proposed ['INV-101', 'INV-103'], expected ['INV-101']" in s.notes


@pytest.mark.req("GOLD-04")
def test_right_actions_without_finishing_fail_completed(deployment, scenario):
    task = next(t for t in TASKS if t.id == "G-PAY-01")
    invoices, payees = _facts(scenario, task)
    s = _one(deployment, scenario, task,
             {"stop_reason": "tool_use", "content": [_propose("INV-101", invoices, payees, 1)]},
             {"stop_reason": "max_tokens", "content": [text("And now I")]})
    assert s.checks == {"completed": False, "proposals": True, "settled": True}
    assert s.outcome == "truncated"


@pytest.mark.req("GOLD-04")
def test_mentions_ignore_case_and_thousands_separators(deployment, scenario):
    task = next(t for t in TASKS if t.id == "G-ASK-01")
    s = _one(deployment, scenario, task,
             {"stop_reason": "tool_use", "content": [tool_use("t", "get_treasury_balance")]},
             {"stop_reason": "end_turn", "content": [text("The treasury holds 100,000.00 USDC.")]})
    assert s.passed and s.refused_proposals == 0


@pytest.mark.req("GOLD-04")
def test_a_right_answer_without_looking_is_a_failure(deployment, scenario):
    """must_call: "100000" is the right number, but the agent never read the balance. It was
    guessed or remembered, and a guess that happens to be right today is a hallucination
    tomorrow."""
    task = next(t for t in TASKS if t.id == "G-ASK-01")
    s = _one(deployment, scenario, task,
             {"stop_reason": "end_turn", "content": [text("The treasury holds 100000.00 USDC.")]})
    assert s.checks["mentions"] and not s.checks["must_call"] and not s.passed


@pytest.mark.req("GOLD-04")
def test_trying_to_pay_on_a_question_is_a_failure_even_when_refused(deployment, scenario):
    """must_not_call: asked *why* INV-104 can't be paid, the agent tries to pay it. The policy
    refuses, so no proposal exists and `proposals` passes, but the attempt itself is the bug."""
    task = next(t for t in TASKS if t.id == "G-ASK-03")
    invoices, payees = _facts(scenario, task)
    s = _one(deployment, scenario, task,
             {"stop_reason": "tool_use", "content": [_propose("INV-104", invoices, payees, 1)]},
             {"stop_reason": "end_turn", "content": [text("INV-104 is disputed.")]})
    assert s.checks["proposals"] and s.checks["mentions"] and not s.checks["must_not_call"]
    assert s.refused_proposals == 1


@pytest.mark.req("GOLD-04")
def test_a_refused_proposal_is_a_metric_not_a_failure(deployment, scenario):
    """Proposing INV-104 then explaining is sloppy (the agent should have checked first), but
    nothing wrong happened. It is counted, not failed: M6 tracks how often the model *tries*."""
    task = next(t for t in TASKS if t.id == "G-HOLD-01")
    invoices, payees = _facts(scenario, task)
    s = _one(deployment, scenario, task,
             {"stop_reason": "tool_use", "content": [_propose("INV-104", invoices, payees, 1)]},
             {"stop_reason": "end_turn", "content": [text("INV-104 is disputed, so I did not pay it.")]})
    assert s.passed and s.refused_proposals == 1


@pytest.mark.req("GOLD-04")
def test_an_operator_proposal_from_setup_is_not_credited_to_the_agent(deployment, scenario):
    task = next(t for t in TASKS if t.id == "G-HOLD-06")   # INV-101 already proposed by an operator
    s = _one(deployment, scenario, task, {"stop_reason": "end_turn", "content": [text("Already proposed.")]})
    assert s.passed


# ---- GOLD-05: statistics ---------------------------------------------------------------------

@pytest.mark.req("GOLD-05")
@pytest.mark.parametrize("passed, n, low, high", [
    (8, 10, 0.4902, 0.9433), (0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0), (50, 100, 0.4038, 0.5962)])
def test_wilson_interval_matches_reference_values(passed, n, low, high):
    lo, hi = stats.wilson(passed, n)
    assert lo == pytest.approx(low, abs=1e-4) and hi == pytest.approx(high, abs=1e-4)


@pytest.mark.req("GOLD-05")
def test_pass_at_k_and_pass_hat_k():
    assert stats.pass_at_k(10, 3, 1) == pytest.approx(0.3)
    assert stats.pass_hat_k(10, 3, 1) == pytest.approx(0.3)
    assert stats.pass_at_k(10, 3, 3) == pytest.approx(1 - 35 / 120)      # 1 - C(7,3)/C(10,3)
    assert stats.pass_hat_k(10, 9, 5) == pytest.approx(126 / 252)        # C(9,5)/C(10,5) = 0.5
    assert stats.pass_at_k(5, 0, 5) == 0.0 and stats.pass_hat_k(5, 5, 5) == 1.0
    with pytest.raises(ValueError):
        stats.pass_at_k(3, 1, 4)


@pytest.mark.req("GOLD-05")
def test_a_flaky_agents_true_rate_lies_in_the_reported_interval(deployment, scenario):
    """GOLD-05: an agent that fails 30% of trials at random (seeded). The report's 95% interval
    contains its true pass rate (70%), and pass^k is far below pass@k: capable, not reliable."""
    scores = runner.run_suite(TASKS, flaky(scenario, p_fail=0.3), deployment, scenario, trials=4)
    summary = runner.summarise(scores)
    low, high = summary["overall"]["ci95"]
    assert summary["overall"]["trials"] == 4 * len(TASKS)
    assert low <= 0.7 <= high
    tasks = summary["tasks"].values()
    mean_at = sum(t["pass@k"] for t in tasks) / len(tasks)
    mean_hat = sum(t["pass^k"] for t in tasks) / len(tasks)
    assert mean_at > 0.9 and mean_hat < 0.5


# ---- GOLD-06: runner and report --------------------------------------------------------------

@pytest.mark.req("GOLD-06")
def test_trials_are_isolated(deployment, scenario):
    """GOLD-06: three trials of the same paying task all pass. If trial 1's payment leaked,
    trial 2 would be refused as a duplicate. And the chain is back where it started."""
    task = next(t for t in TASKS if t.id == "G-PAY-04")
    northwind = next(v.payout_address for v in scenario.vendors if v.vendor_id == "V-NORTHWIND")
    token = deployment.token
    before = token.balance_of(northwind)
    scores = runner.run_suite([task], solver(scenario), deployment, scenario, trials=3)
    assert [s.passed for s in scores] == [True, True, True]
    assert token.balance_of(northwind) == before


@pytest.mark.req("GOLD-06")
def test_fallback_served_trials_are_excluded_from_rates(deployment, scenario, tmp_path):
    task = next(t for t in TASKS if t.id == "G-ASK-01")
    served_by_fallback = {"stop_reason": "end_turn", "model": "claude-sonnet-5-5",
                          "content": [{"type": "fallback", "from": {"model": "claude-opus-5-5"},
                                       "to": {"model": "claude-sonnet-5-5"}}, text("100000")],
                          "usage": {"input_tokens": 5, "output_tokens": 5,
                                    "iterations": [{"type": "fallback_message"}]}}
    make = lambda t, trial: (_turns(served_by_fallback) if trial == 0 else solver(scenario)(t, trial))
    scores = runner.run_suite([task], make, deployment, scenario, trials=3)
    summary = runner.write_report(scores, tmp_path)
    assert summary["fallback_trials_excluded"] == 1 and summary["overall"]["trials"] == 2
    report = (tmp_path / "report.md").read_text()
    assert "| G-ASK-01 | ask | 2/2 | 100% | 100% | – |" in report
    assert "Fallback-served trials excluded: 1" in report
    assert json.loads((tmp_path / "results.json").read_text())["trials"][0]["fallback"] is True


@pytest.mark.req("GOLD-06")
def test_the_cli_replays_recorded_cassettes_end_to_end(deployment, scenario, tmp_path):
    """GOLD-06: the path M8 will use with real recordings. Record cassettes (from the reference
    solver), then `python -m agentqa.evals --provider replay` starts its own chain, replays
    them, and reports. A missing cassette is a clear exit code 2, not a crash mid-run."""
    from agentqa.evals import __main__ as cli
    from agentqa.providers import Cassette

    tasks = [t for t in TASKS if t.id in ("G-PAY-04", "G-HOLD-03")]
    make = solver(scenario)
    runner.run_suite(tasks, lambda t, i: cli._SavingRecorder(
        make(t, i), Cassette(model="claude-opus-5-5"), tmp_path / "cassettes" / f"{t.id}-{i}.json"),
        deployment, scenario, trials=2)
    common = ["--tasks", "G-PAY-04,G-HOLD-03", "--cassettes", str(tmp_path / "cassettes")]

    assert cli.main(["--provider", "replay", "--trials", "2", "--out", str(tmp_path / "out"), *common]) == 0
    results = json.loads((tmp_path / "out" / "results.json").read_text())
    assert results["summary"]["overall"]["passed"] == 4
    assert cli.main(["--provider", "replay", "--trials", "3", "--out", str(tmp_path / "out3"), *common]) == 2
