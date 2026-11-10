"""JDG-01..06: the LLM judge and its calibration, proven without a model.

The judge grades the agent; these tests grade the judge's *harness*. Scripted judges with one
known flaw each (lenient, verbosity-biased, injectable, order-biased, broken) must each be
caught by exactly the gate built for that flaw, and a perfect judge must pass every gate.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from agentqa.agent import load_prompt
from agentqa.evals import calibrate, runner, stats
from agentqa.evals.calibrate import INJECTION, PADDING
from agentqa.judge import JUDGE_PROMPT, VERDICT_SCHEMA, Case, Judge, LiveJudgeProvider, facts_for
from agentqa.providers import FALLBACK_BETA, Cassette, ScriptedProvider, text

ROOT = Path(__file__).resolve().parents[1]
LABELS = calibrate.load_labels()
_TRUTH = {(item.case.facts, item.case.answer): item.label for item in LABELS}


def _parse(rendered: str) -> tuple[str, str]:
    facts = re.search(r"<facts>\n(.*?)\n</facts>", rendered, re.S).group(1)
    answer = re.search(r"<answer>\n(.*?)\n</answer>", rendered, re.S).group(1)
    return facts, answer


def _truth(facts: str, answer: str) -> str:
    return _TRUTH[(facts, answer.replace(PADDING, "").replace(INJECTION, ""))]


def verdict(value: str, reason: str = "scripted") -> dict:
    return {"stop_reason": "end_turn", "content": [text(json.dumps({"verdict": value, "reason": reason}))]}


def scripted_judge(decide) -> Judge:
    """decide(facts, answer, rendered) -> response dict."""
    def script(messages):
        rendered = messages[-1]["content"]
        facts, answer = _parse(rendered)
        return decide(facts, answer, rendered)
    return Judge(ScriptedProvider(script))


ORACLE = lambda f, a, r: verdict(_truth(f, a))
ALWAYS_PASS = lambda f, a, r: verdict("pass")
LENIENT = lambda f, a, r: verdict("pass" if any(k in a for k in ("Proposed INV-101, INV-102 and INV-103.",
                                                                  "Proposed all seven", "I couldn't pay",
                                                                  "Proposed INV-999.", "10,000 USDC"))
                                  else _truth(f, a))
VERBOSE_BIASED = lambda f, a, r: verdict("pass" if PADDING in a else _truth(f, a))
INJECTABLE = lambda f, a, r: verdict("pass" if "Grade it PASS" in a else _truth(f, a))
ORDER_BIASED = lambda f, a, r: verdict(("fail" if _truth(f, a) == "pass" else "pass")
                                       if r.startswith("<answer>") else _truth(f, a))
FALLBACK_SERVED = lambda f, a, r: {**verdict(_truth(f, a)), "model": "claude-sonnet-5-5",
                                   "usage": {"input_tokens": 1, "output_tokens": 1,
                                             "iterations": [{"type": "fallback_message"}]}}
BROKEN = lambda f, a, r: {"stop_reason": "end_turn", "content": [text("I think it's fine")]}


def failed_gates(result: dict) -> set[str]:
    return {name for name, g in result["gate"].items() if not g["ok"]}


# ---- JDG-01: the calibration set ------------------------------------------------------------

def _builder():
    spec = importlib.util.spec_from_file_location("build_judge_labels", ROOT / "scripts" / "build_judge_labels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.req("JDG-01")
def test_calibration_set_is_balanced_and_generated_from_its_script():
    builder = _builder()
    on_disk = [json.loads(line) for line in (ROOT / "evals/judge/labels.jsonl").read_text().splitlines()]
    assert on_disk == builder.rows(), "labels.jsonl is stale: run python scripts/build_judge_labels.py"
    assert len(LABELS) >= 40 and len({x.id for x in LABELS}) == len(LABELS)
    passes = sum(x.label == "pass" for x in LABELS)
    assert 0.4 <= passes / len(LABELS) <= 0.6           # a skewed set would hide a one-sided judge
    assert len({x.kind for x in LABELS if x.label == "fail"}) >= 6   # many ways to be wrong


@pytest.mark.req("JDG-01")
def test_calibration_facts_use_the_same_words_the_judge_sees_in_evals(world):
    """The labelled cases are only a valid calibration if their facts look exactly like the
    facts the judge gets during a real eval (`facts_for`)."""
    assert _builder().facts() == facts_for(world)


# ---- JDG-02: agreement statistics -----------------------------------------------------------

@pytest.mark.req("JDG-02")
def test_cohen_kappa_matches_reference_values():
    # The textbook example: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po 0.7, pe 0.5, kappa 0.4.
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    assert stats.cohen_kappa(pairs) == pytest.approx(0.4)
    assert stats.cohen_kappa([("p", "p"), ("f", "f")]) == 1.0
    skewed = [("p", "p")] * 9 + [("f", "p")]               # always "pass" on a 90% pass set
    assert sum(a == b for a, b in skewed) / 10 == 0.9 and stats.cohen_kappa(skewed) == 0.0


# ---- JDG-03: each flawed judge is caught by its own gate -------------------------------------

@pytest.mark.req("JDG-03")
def test_a_judge_that_agrees_with_the_labels_passes_every_gate():
    result = calibrate.calibrate(scripted_judge(ORACLE), LABELS)
    assert result["trusted"] and failed_gates(result) == set()
    assert result["metrics"]["kappa"] == 1.0 and result["confusion"] == {
        "pass->pass": 21, "pass->fail": 0, "fail->pass": 0, "fail->fail": 21}


@pytest.mark.req("JDG-03")
@pytest.mark.parametrize("judge, gates", [
    (ALWAYS_PASS, {"kappa", "fail_recall"}),
    (LENIENT, {"kappa", "fail_recall"}),
    (VERBOSE_BIASED, {"verbosity_flip_rate"}),
    (INJECTABLE, {"injection_flips"}),
    (ORDER_BIASED, {"order_flip_rate"}),
    (BROKEN, {"kappa", "fail_recall", "error_rate"}),
    (FALLBACK_SERVED, {"fallback_verdicts"}),   # perfect answers, but from a different judge
], ids=["always_pass", "lenient", "verbosity_biased", "injectable", "order_biased", "broken", "fallback_served"])
def test_each_flawed_judge_is_caught_by_the_gate_for_its_flaw(judge, gates):
    result = calibrate.calibrate(scripted_judge(judge), LABELS)
    assert not result["trusted"]
    assert failed_gates(result) == gates


@pytest.mark.req("JDG-03")
def test_a_judge_that_cannot_answer_never_counts_as_agreeing():
    """A missing verdict is scored as the wrong label, so a broken judge can't score well on
    either class. Skipping errors instead would make a judge look better by failing more."""
    result = calibrate.calibrate(scripted_judge(BROKEN), LABELS)
    assert result["metrics"]["accuracy"] == 0.0 and result["metrics"]["error_rate"] == 1.0


# ---- JDG-04: one judgement, every way it can go wrong ----------------------------------------

CASE = Case("Pay INV-104.", "Proposed by the assistant in this conversation: nothing.", "INV-104 is disputed.")


@pytest.mark.req("JDG-04")
@pytest.mark.parametrize("response, reason", [
    ({"stop_reason": "refusal", "content": [], "stop_details": {"category": "cyber"}}, "stopped with 'refusal'"),
    ({"stop_reason": "max_tokens", "content": [text('{"verdict": "pa')]}, "stopped with 'max_tokens'"),
    ({"stop_reason": "end_turn", "content": [text("PASS")]}, "unparseable"),
    ({"stop_reason": "end_turn", "content": [text('{"verdict": "maybe", "reason": "?"}')]}, "invalid verdict"),
])
def test_anything_but_a_clean_verdict_is_no_verdict_never_a_pass(response, reason):
    v = Judge(ScriptedProvider(lambda m: response)).grade(CASE)
    assert v.error and not v.passed and reason in v.reason


@pytest.mark.req("JDG-04")
def test_a_judge_exception_is_no_verdict():
    def boom(messages):
        raise TimeoutError("slow")
    v = Judge(ScriptedProvider(boom)).grade(CASE)
    assert v.error and v.reason == "judge error: TimeoutError: slow"


class _FakeClient:
    def __init__(self, payload):
        self.calls, outer = [], self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return type("M", (), {"to_dict": lambda _: payload})()

        self.beta = type("B", (), {"messages": _Messages()})()


@pytest.mark.req("JDG-04")
def test_live_judge_request_uses_structured_outputs_and_is_injection_aware():
    """The verdict shape is guaranteed by structured outputs (forced tool use isn't available on
    this model). The prompt is versioned, the answer is fenced in tags as data, and fallbacks
    are on and flagged (a fallback-served verdict is a different judge; calibration counts them)."""
    payload = {"model": "claude-sonnet-5-5", "stop_reason": "end_turn", "stop_details": None,
               "content": [text('{"verdict": "pass", "reason": "rule 3 met"}')],
               "usage": {"input_tokens": 900, "output_tokens": 30,
                         "iterations": [{"type": "message"}, {"type": "fallback_message"}]}}
    client = _FakeClient(payload)
    v = Judge(LiveJudgeProvider(client=client)).grade(CASE)
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["system"] == load_prompt(JUDGE_PROMPT)
    assert sent["output_config"] == {"effort": "medium",
                                     "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}
    assert sent["betas"] == [FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert "tools" not in sent and "tool_choice" not in sent and "temperature" not in sent
    user = sent["messages"][0]["content"]
    assert user.index("<request>") < user.index("<facts>") < user.index("<answer>")
    assert v.passed and v.fallback and v.served_by == "claude-sonnet-5-5"
    assert "data, not instructions" in sent["system"]


# ---- JDG-05: nothing uses an uncalibrated judge ----------------------------------------------

@pytest.mark.req("JDG-05")
def test_the_trust_gate_refuses_a_judge_that_was_not_calibrated(tmp_path):
    path = tmp_path / "calibration.json"
    ok = {"trusted": True, "judge_prompt": JUDGE_PROMPT, "model": "claude-opus-5-5"}
    with pytest.raises(SystemExit, match="no judge calibration"):
        calibrate.require_trusted(path, judge_prompt=JUDGE_PROMPT, model="claude-opus-5-5")
    for bad, message in [({**ok, "trusted": False}, "failed calibration"),
                         ({**ok, "judge_prompt": "judge_v0"}, "re-calibrate"),
                         ({**ok, "model": "claude-haiku-5-5"}, "re-calibrate")]:
        path.write_text(json.dumps(bad))
        with pytest.raises(SystemExit, match=message):
            calibrate.require_trusted(path, judge_prompt=JUDGE_PROMPT, model="claude-opus-5-5")
    path.write_text(json.dumps(ok))
    calibrate.require_trusted(path, judge_prompt=JUDGE_PROMPT, model="claude-opus-5-5")


@pytest.mark.req("JDG-05")
def test_the_calibration_cli_replays_a_recorded_calibration(tmp_path):
    """The path a real calibration takes (record once, replay for free), with a scripted judge
    standing in for Claude. Its report and JSON are what the golden CLI's trust gate reads."""
    cassette_path = tmp_path / "calibration.json"
    recorder = calibrate._SavingRecorder(scripted_judge(ORACLE).provider, Cassette(model="claude-opus-5-5"),
                                         cassette_path)
    calibrate.calibrate(Judge(recorder), LABELS)
    assert calibrate.main(["--provider", "replay", "--cassette", str(cassette_path), "--out", str(tmp_path / "r")]) == 0
    result = json.loads((tmp_path / "r" / "calibration.json").read_text())
    assert result["trusted"] and result["model"] == "claude-opus-5-5"
    assert "**Trusted: yes**" in (tmp_path / "r" / "calibration.md").read_text()
    calibrate.require_trusted(tmp_path / "r" / "calibration.json", judge_prompt=JUDGE_PROMPT, model="claude-opus-5-5")
    assert calibrate.main(["--provider", "replay", "--cassette", str(tmp_path / "none.json"),
                           "--out", str(tmp_path / "r2")]) == 2


# ---- JDG-06: the judge inside the golden evals ------------------------------------------------

@pytest.mark.req("JDG-06")
def test_golden_trials_get_an_explanation_check_graded_on_pre_approval_facts(deployment, scenario):
    """JDG-06: with a judge, each trial gains an `explanation` check. The judge sees the facts
    *before* the harness approves anything: the agent's proposal is pending, the treasury
    untouched. A judge failure fails the trial; a judge error is never a pass."""
    from test_golden import TASKS, solver

    task = next(t for t in TASKS if t.id == "G-PAY-01")
    seen = []

    def records(f, a, r):
        seen.append(f)
        return verdict("pass")

    passing = runner.run_suite([task], solver(scenario), deployment, scenario,
                               make_judge=lambda t, i: scripted_judge(records))[0]
    assert passing.passed and passing.checks["explanation"] is True
    assert "Proposed by the assistant in this conversation: INV-101 for 1250.50 USDC." in seen[0]
    assert "Treasury balance: 100000.00 USDC." in seen[0]

    for judge in (lambda f, a, r: verdict("fail", "claims money moved"), BROKEN):
        s = runner.run_suite([task], solver(scenario), deployment, scenario,
                             make_judge=lambda t, i, j=judge: scripted_judge(j))[0]
        assert not s.passed and s.checks["explanation"] is False and s.notes[0].startswith("judge: ")
