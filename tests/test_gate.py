"""GATE-01..05: the regression gate blocks a worse candidate and passes a neutral one.

Baselines and candidates are real scripted runs, written as the eval CLI writes them
(`results.json`). A "worse prompt" is simulated by a worse scripted agent: what the gate sees
is identical, because it compares results, not prompts. The real prompt-change demonstration
needs live runs (M8).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentqa.evals import gate, redteam, runner, stats
from test_golden import TASKS, flaky, over_eager, solver
from test_redteam import agent as redteam_agent

CONFIG = json.loads(gate.CONFIG.read_text())
META = {"model": "claude-opus-5-5", "prompt": "system_v1", "provider": "scripted", "trials": 3}


def golden_results(make, deployment, scenario, tmp_path: Path, name: str, trials: int = 3) -> dict:
    scores = runner.run_suite(TASKS, make, deployment, scenario, trials=trials)
    runner.write_report(scores, tmp_path / name, name, meta=META)
    return json.loads((tmp_path / name / "results.json").read_text())


def failed(checks) -> set[str]:
    return {c.name for c in checks if not c.ok}


def worse_on_holds(scenario):
    """A plausible bad prompt change: still pays correctly, but over-eager whenever it should hold."""
    good, eager = solver(scenario), over_eager(scenario)
    return lambda task, trial: eager(task, trial) if task.category in ("hold", "ask") else good(task, trial)


# ---- GATE-01: the tests behind the gate -------------------------------------------------------

@pytest.mark.req("GATE-01")
def test_fisher_and_sign_test_match_reference_values():
    assert stats.fisher_worse(10, 10, 5, 10) == pytest.approx(3003 / 184756)    # C(15,5)/C(20,10) = 0.0163
    assert stats.fisher_worse(8, 10, 8, 10) > 0.5                              # same rate: no evidence
    assert stats.fisher_worse(5, 10, 10, 10) == pytest.approx(1.0)             # candidate better
    assert stats.sign_test_worse(8, 0) == pytest.approx(1 / 256)
    assert stats.sign_test_worse(3, 3) == pytest.approx(42 / 64)
    assert stats.sign_test_worse(0, 0) == 1.0


# ---- GATE-02: worse is blocked, neutral passes -------------------------------------------------

@pytest.fixture(scope="module")
def baseline(deployment, scenario, tmp_path_factory):
    return golden_results(flaky(scenario, p_fail=0.05, seed=1), deployment, scenario,
                          tmp_path_factory.mktemp("gate"), "baseline")


@pytest.mark.req("GATE-02")
def test_a_worse_candidate_is_blocked(baseline, deployment, scenario, tmp_path):
    candidate = golden_results(worse_on_holds(scenario), deployment, scenario, tmp_path, "worse")
    checks = gate.compare_golden(baseline, candidate, CONFIG)
    assert {"tasks", "pooled"} <= failed(checks)
    tasks = next(c for c in checks if c.name == "tasks")
    assert "16 worse" in tasks.detail and "G-HOLD-01" in tasks.detail


@pytest.mark.req("GATE-02")
def test_a_neutral_candidate_with_its_own_noise_passes(baseline, deployment, scenario, tmp_path):
    """Same agent quality, different random failures (another seed). Some tasks move each way;
    that is noise, and the gate must not block it."""
    candidate = golden_results(flaky(scenario, p_fail=0.05, seed=2), deployment, scenario, tmp_path, "neutral")
    checks = gate.compare_golden(baseline, candidate, CONFIG)
    assert failed(checks) == set()
    moved = [k for k, t in candidate["summary"]["tasks"].items() if t["passed"] != baseline["summary"]["tasks"][k]["passed"]]
    assert moved, "the two seeds should differ somewhere, or this test proves nothing"


@pytest.mark.req("GATE-02")
def test_a_broadly_flakier_candidate_is_blocked(baseline, deployment, scenario, tmp_path):
    candidate = golden_results(flaky(scenario, p_fail=0.4, seed=3), deployment, scenario, tmp_path, "flaky")
    assert "tasks" in failed(gate.compare_golden(baseline, candidate, CONFIG))


@pytest.mark.req("GATE-02")
def test_a_candidate_that_skips_tasks_is_blocked(baseline):
    candidate = copy.deepcopy(baseline)
    del candidate["summary"]["tasks"]["G-HOLD-09"]
    assert "coverage" in failed(gate.compare_golden(baseline, candidate, CONFIG))


# ---- GATE-03: budgets ---------------------------------------------------------------------------

def _with_usage(results: dict, input_tokens: int, output_tokens: int, seconds: float) -> dict:
    out = copy.deepcopy(results)
    for t in out["trials"]:
        t["input_tokens"], t["output_tokens"], t["duration_s"] = input_tokens, output_tokens, seconds
    return out


@pytest.mark.req("GATE-03")
def test_cost_and_latency_budgets(baseline):
    base = _with_usage(baseline, 20_000, 2_000, 30.0)          # $0.08 + $0.04 = $0.12 per trial
    assert failed(gate.compare_golden(base, _with_usage(baseline, 22_000, 2_100, 35.0), CONFIG)) == set()
    pricier = gate.compare_golden(base, _with_usage(baseline, 30_000, 3_000, 30.0), CONFIG)   # +50%
    assert failed(pricier) == {"cost"} and "$0.1800/trial" in next(c.detail for c in pricier if c.name == "cost")
    assert failed(gate.compare_golden(base, _with_usage(baseline, 20_000, 2_000, 150.0), CONFIG)) == {"latency"}


@pytest.mark.req("GATE-03")
def test_cost_is_priced_per_model_and_excludes_fallback_trials(baseline):
    base = _with_usage(baseline, 20_000, 2_000, 1.0)
    sonnet = copy.deepcopy(base)
    sonnet["meta"]["model"] = "claude-sonnet-5-5"               # half the price per token
    cost = next(c for c in gate.compare_golden(base, sonnet, CONFIG) if c.name == "cost")
    assert cost.ok and "$0.0600/trial" in cost.detail
    fallback = copy.deepcopy(base)
    fallback["trials"][0]["input_tokens"] = 10**9                # a fallback trial is not priced
    fallback["trials"][0]["fallback"] = True
    assert next(c for c in gate.compare_golden(base, fallback, CONFIG) if c.name == "cost").ok
    unknown = copy.deepcopy(base)
    unknown["meta"]["model"] = "claude-unknown"
    with pytest.raises(SystemExit, match="no price"):
        gate.compare_golden(base, unknown, CONFIG)


# ---- GATE-04: the red team inside the gate ------------------------------------------------------

def redteam_results(style, deployment, scenario, tmp_path, name):
    attacks, benign = redteam.load_corpus(scenario)
    a, b = redteam.run_redteam(attacks, benign, redteam_agent(style), deployment, scenario)
    redteam.write_report(a, b, tmp_path / name, name, meta=META)
    return json.loads((tmp_path / name / "results.json").read_text())


@pytest.mark.req("GATE-04")
def test_the_gate_blocks_more_steerable_or_more_refusing_candidates(deployment, scenario, tmp_path):
    careful = redteam_results("careful", deployment, scenario, tmp_path, "careful")
    assert failed(gate.compare_redteam(careful, careful, CONFIG)) == set()
    gullible = redteam_results("gullible", deployment, scenario, tmp_path, "gullible")
    assert failed(gate.compare_redteam(careful, gullible, CONFIG)) == {"redteam_attempts"}
    paranoid = redteam_results("paranoid", deployment, scenario, tmp_path, "paranoid")
    assert failed(gate.compare_redteam(careful, paranoid, CONFIG)) == {"over_blocking"}


@pytest.mark.req("GATE-04")
def test_any_executed_attack_blocks_whatever_the_statistics(deployment, scenario, tmp_path):
    careful = redteam_results("careful", deployment, scenario, tmp_path, "careful")
    breached = copy.deepcopy(careful)
    breached["summary"]["overall"]["executed"] = 1
    breached["summary"]["executed_attacks"] = ["A-DUP-01"]
    checks = gate.compare_redteam(careful, breached, CONFIG)
    assert failed(checks) == {"redteam_executed"} and "A-DUP-01" in checks[0].detail


# ---- GATE-05: the CLI, traces and the CI workflow ----------------------------------------------

@pytest.mark.req("GATE-05")
def test_the_gate_cli_exits_1_on_regression_and_writes_its_table(baseline, deployment, scenario, tmp_path):
    (tmp_path / "base.json").write_text(json.dumps(baseline))
    worse = golden_results(worse_on_holds(scenario), deployment, scenario, tmp_path, "w")
    (tmp_path / "worse.json").write_text(json.dumps(worse))
    assert gate.main(["--baseline", str(tmp_path / "base.json"), "--candidate", str(tmp_path / "base.json")]) == 0
    assert gate.main(["--baseline", str(tmp_path / "base.json"), "--candidate", str(tmp_path / "worse.json"),
                      "--out", str(tmp_path / "gate.md")]) == 1
    assert "BLOCKED" in (tmp_path / "gate.md").read_text()


@pytest.mark.req("GATE-05")
def test_every_trial_can_leave_a_trace(deployment, scenario, tmp_path):
    task = next(t for t in TASKS if t.id == "G-PAY-01")
    runner.run_suite([task], solver(scenario), deployment, scenario, trials=2, trace_dir=tmp_path / "traces")
    trace = json.loads((tmp_path / "traces" / "G-PAY-01-1.json").read_text())
    assert trace["passed"] and trace["prompt_version"] == "system_v1" and trace["duration_s"] >= 0
    assert [c["name"] for c in trace["tool_calls"]][-1] == "propose_payment"
    assert trace["messages"][0] == {"role": "user", "content": task.prompt}


@pytest.mark.req("GATE-05")
def test_the_eval_gate_workflow_is_manual_or_prompt_triggered_and_keeps_the_key_secret():
    """The live gate runs only on demand or when a prompt or the tools change (cost), reads the
    key only from the repository secret, and never prints it (ENV-D2)."""
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/eval-gate.yml").read_text()
    assert "workflow_dispatch" in text and "src/agentqa/prompts/**" in text and "src/agentqa/tools.py" in text
    assert "${{ secrets.ANTHROPIC_API_KEY }}" in text and "permissions:\n  contents: read" in text
    assert "echo $ANTHROPIC_API_KEY" not in text and "set -x" not in text
    assert "python -m agentqa.evals.gate" in text


@pytest.mark.req("GATE-03")
def test_the_latency_budget_is_a_95th_percentile_not_a_mean_or_a_max(baseline):
    """10% of trials slow → p95 is slow → blocked (a mean would hide it). 2% slow → p95 is
    fine → passes (a max would block on two stragglers)."""
    base = _with_usage(baseline, 20_000, 2_000, 30.0)

    def slow(fraction):
        out = copy.deepcopy(base)
        n = round(len(out["trials"]) * fraction)
        for t in out["trials"][:n]:
            t["duration_s"] = 150.0
        return out
    assert failed(gate.compare_golden(base, slow(0.10), CONFIG)) == {"latency"}
    assert failed(gate.compare_golden(base, slow(0.02), CONFIG)) == set()
