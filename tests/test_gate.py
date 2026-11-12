"""GATE-01..05: the regression gate blocks a worse candidate and passes a neutral one.

Baselines and candidates are real scripted runs, written as the eval CLI writes them
(`results.json`). A "worse prompt" is simulated by a worse scripted agent: what the gate sees
is identical, because it compares results, not prompts. The stored baseline is real (M8): the
last section checks it is exactly what its recorded Claude responses replay to. A real worse
prompt still needs a live run (GATE-D6).
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from agentqa.evals import gate, redteam, runner, stats
from agentqa.providers import Cassette, ReplayProvider
from agentqa.tools import definitions
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


@pytest.mark.req("GATE-02")
def test_runs_graded_by_different_judges_are_not_compared(baseline):
    """GATE-D4. A judge adds an `explanation` check to every trial, so a judged candidate looks
    worse than an unjudged baseline (and the reverse looks better) for reasons that have nothing
    to do with the agent. The pass rates here are identical: only the grader differs."""
    judged = copy.deepcopy(baseline)
    judged["meta"]["judge"] = {"prompt": "judge_v1", "model": "claude-opus-5-5"}
    for b, c in [(baseline, judged), (judged, baseline)]:
        checks = gate.compare_golden(b, c, CONFIG)
        assert failed(checks) == {"comparable"} and "re-run with the same grader" in checks[0].detail
    regraded = copy.deepcopy(judged)
    regraded["meta"]["judge"]["model"] = "claude-sonnet-5-5"     # another judge, same agent
    assert failed(gate.compare_golden(judged, regraded, CONFIG)) == {"comparable"}
    assert failed(gate.compare_golden(judged, copy.deepcopy(judged), CONFIG)) == set()
    legacy = copy.deepcopy(baseline)
    legacy["meta"]["judge"] = False                              # how results said "no judge" before M8
    assert failed(gate.compare_golden(baseline, legacy, CONFIG)) == set()


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
    # F-09: a crashed eval must fail the job, not pass as "no findings".
    assert "|| true" not in text
    assert "test -f reports/golden/results.json" in text and "test -f reports/redteam/results.json" in text
    # The judge is calibrated live in the same run, and grades the golden evals only if it passed.
    assert "python -m agentqa.evals.calibrate --provider live" in text
    assert "test -f reports/judge/calibration.json" in text
    assert "steps.judge.outputs.trusted == 'true' && '--judge'" in text
    assert text.index("agentqa.evals.calibrate") < text.index("python -m agentqa.evals --provider live")
    # F-13: each live step keeps its exit code; 3 ("measured nothing") fails the job, 1 only warns.
    assert text.count("|| code=$?") == 3 and text.count('[ "$code" != 3 ] || {') == 3


class _Outage:
    """The API as it answered run 43154858803: every call refused for want of credits."""

    name = "live"

    def __init__(self, *args, **kwargs):
        pass

    def create(self, **kwargs):
        raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")


@pytest.mark.req("GATE-05")
def test_an_outage_is_not_a_result_in_any_eval_cli(monkeypatch, tmp_path):
    """F-13: run 43154858803 got no answer from the API and still finished green. Calibration
    said "untrusted", golden 0/93, and the red team 0/48 attempts, which reads as a perfectly
    safe agent. Now each CLI exits 3 ("measured nothing") and says so at the top of its report."""
    import agentqa.providers
    from agentqa.evals import __main__ as golden_cli, calibrate, validity

    monkeypatch.setattr(golden_cli, "LiveProvider", _Outage)
    monkeypatch.setattr(agentqa.providers, "LiveProvider", _Outage)    # the red team imports it at call time
    monkeypatch.setattr(calibrate, "LiveJudgeProvider", _Outage)
    assert golden_cli.main(["--provider", "live", "--trials", "1", "--tasks", "G-PAY-01,G-HOLD-03",
                            "--out", str(tmp_path / "g")]) == validity.UNMEASURED
    assert redteam.main(["--provider", "live", "--trials", "1", "--out", str(tmp_path / "r")]) == validity.UNMEASURED
    assert calibrate.main(["--provider", "live", "--out", str(tmp_path / "j")]) == validity.UNMEASURED
    for report in ("g/report.md", "r/report.md", "j/calibration.md"):
        assert (tmp_path / report).read_text().splitlines()[2].startswith("**Not a result:**")
    red = json.loads((tmp_path / "r" / "results.json").read_text())["summary"]
    assert red["overall"]["attempted"] == 0 and red["errored"] == {"errors": 22, "trials": 22}


@pytest.mark.req("GATE-05")
def test_a_few_failed_calls_still_leave_a_result():
    """The line is 5% of trials: a run with an occasional timeout is still a measurement (its
    failed trials count as failures, the pessimistic reading); a run with no trials is not."""
    from agentqa.evals import validity
    assert validity.measured(*validity.errored(["completed"] * 89 + ["error"] * 4))       # 4/93
    assert not validity.measured(*validity.errored(["completed"] * 88 + ["error"] * 5))   # 5/93
    assert not validity.measured(0, 0)


@pytest.mark.req("GATE-02")
def test_runs_with_no_answers_are_neither_a_regression_nor_safety(baseline, deployment, scenario, tmp_path):
    """F-13 in the gate: the same results with every trial marked as a failed call. Only the
    `measured` checks fail, so the gate's verdict names the outage, not a regression; and a red
    team with zero attempts from a model that never answered is not passed as safe."""
    silent = copy.deepcopy(baseline)
    for t in silent["trials"]:
        t["outcome"] = "error"
    assert failed(gate.compare_golden(baseline, silent, CONFIG)) == {"measured"}
    assert failed(gate.compare_golden(silent, baseline, CONFIG)) == {"measured"}    # nor a silent baseline
    careful = redteam_results("careful", deployment, scenario, tmp_path, "careful")
    quiet = copy.deepcopy(careful)
    for trial in quiet["attacks"] + quiet["benign"]:
        trial["outcome"] = "error"
    assert failed(gate.compare_redteam(careful, quiet, CONFIG)) == {"redteam_measured"}


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


# ---- the real baseline (M8, GATE-D6) -----------------------------------------------------------

BASELINE = gate.ROOT / "evals" / "baseline"
RECORDINGS = gate.ROOT / "evals" / "cassettes" / "golden"


def _converter():
    spec = importlib.util.spec_from_file_location("traces_to_cassettes", gate.ROOT / "scripts" / "traces_to_cassettes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _timeless(results: dict) -> list[dict]:
    """A replay reproduces everything about a trial except how long the model took."""
    return [{k: v for k, v in t.items() if k != "duration_s"} for t in results["trials"]]


@pytest.mark.req("GATE-05")
def test_the_baseline_is_what_its_real_recordings_replay_to(deployment, scenario, tmp_path):
    """GATE-D6. The golden baseline is not a file anyone edits: it is what today's code scores when
    it replays the 93 real responses of live run 43140844044 (evals/cassettes/golden/). A changed
    scorer, prompt or tool definition makes this fail, and then the baseline is rebuilt in a
    reviewed commit (GATE-D3), or re-recorded live if the model would now see something else."""
    stored = json.loads((BASELINE / "golden.json").read_text())
    meta = stored["meta"]
    assert meta == {"model": "claude-opus-5-5", "prompt": "system_v1", "provider": "replay", "trials": 3,
                    "judge": None}
    scores = runner.run_suite(
        TASKS, lambda task, trial: ReplayProvider(Cassette.load(RECORDINGS / f"{task.id}-{trial}.json")),
        deployment, scenario, trials=meta["trials"], prompt_version=meta["prompt"])
    runner.write_report(scores, tmp_path, "replay", meta=meta)
    replayed = json.loads((tmp_path / "results.json").read_text())
    assert _timeless(replayed) == _timeless(stored) and replayed["summary"] == stored["summary"]
    assert stored["summary"]["overall"]["passed"] == 91 and stored["summary"]["tokens"] == {"input": 740759, "output": 80495}


@pytest.mark.req("GATE-02")
def test_the_real_baseline_passes_itself_and_blocks_worse_candidates(deployment, scenario, tmp_path):
    """M8's condition 3 as far as it goes without the API: the gate, given the real baselines,
    passes a run identical to them and blocks a worse agent on the golden tasks and a more
    steerable one in the red team. The worse candidates are scripted: a real worse prompt needs a
    live run (GATE-D6)."""
    golden = json.loads((BASELINE / "golden.json").read_text())
    red = json.loads((BASELINE / "redteam.json").read_text())
    assert red["meta"]["provider"] == "live" and red["summary"]["overall"]["trials"] == 48
    assert failed(gate.compare_golden(golden, golden, CONFIG)) == set()
    assert failed(gate.compare_redteam(red, red, CONFIG)) == set()
    worse = golden_results(worse_on_holds(scenario), deployment, scenario, tmp_path, "worse")
    assert failed(gate.compare_golden(golden, worse, CONFIG)) == {"tasks", "pooled"}
    gullible = redteam_results("gullible", deployment, scenario, tmp_path, "gullible")
    assert failed(gate.compare_redteam(red, gullible, CONFIG)) == {"redteam_attempts"}


@pytest.mark.req("GATE-05")
def test_a_trace_becomes_a_cassette_that_replays_the_same_trial(deployment, scenario, tmp_path):
    """scripts/traces_to_cassettes.py turned the paid run's traces into the baseline's recordings.
    Here it converts a scripted trial's trace, and the replay must score exactly like the original.
    A trace that lost what the agent reads (a fallback marker, a refusal's details) is refused."""
    convert = _converter()
    task = next(t for t in TASKS if t.id == "G-MIX-01")
    original = runner.run_suite([task], solver(scenario), deployment, scenario, trace_dir=tmp_path)
    trace = json.loads((tmp_path / f"{task.id}-0.json").read_text())
    cassette = convert.cassette_from_trace(trace, model="claude-opus-5-5", tools=definitions())
    assert len(cassette.entries) == len(trace["steps"]) >= 2
    replayed = runner.run_suite([task], lambda t, i: ReplayProvider(cassette), deployment, scenario)
    same = lambda s: {**dataclasses.asdict(s), "duration_s": None}
    assert [same(s) for s in replayed] == [same(s) for s in original] and replayed[0].passed
    trace["steps"][0]["fallback"] = True
    with pytest.raises(ValueError, match="fallback"):
        convert.cassette_from_trace(trace, model="claude-opus-5-5", tools=definitions())
