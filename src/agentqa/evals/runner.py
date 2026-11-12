"""Run tasks × trials in isolation, then summarise with honest statistics.

Isolation: every trial starts from `evm_snapshot` and a fresh SQLite file for the books and
transfer API, and is reverted afterwards, so a payment in trial 1 can't leak into trial 2.

Fallback-served trials (AGT-D3) measured a different model, so they are excluded from the
rates and counted separately.
"""

from __future__ import annotations

import json
import tempfile
import time
from contextlib import contextmanager
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import transfer_api

from ..agent import Agent
from ..judge import Judge
from ..providers import Provider
from ..scenarios import Scenario
from ..world import Deployment, World
from .dataset import CATEGORIES, GoldenTask, apply_setup
from .scoring import TrialScore, score_trial
from .stats import pass_at_k, pass_hat_k, wilson

ProviderFactory = Callable[[GoldenTask, int], Provider]
JudgeFactory = Callable[[GoldenTask, int], Judge]


@contextmanager
def trial_world(deployment: Deployment, scenario: Scenario, db_path: Path):
    """One isolated trial: a chain snapshot and fresh books, reverted afterwards."""
    chain = deployment.chain
    snapshot = chain.snapshot()
    engine = transfer_api.make_engine(f"sqlite:///{db_path}")
    try:
        yield World.open(deployment, scenario, engine)
    finally:
        engine.dispose()
        chain.revert(snapshot)


def run_suite(tasks: list[GoldenTask], make_provider: ProviderFactory, deployment: Deployment,
              scenario: Scenario, *, trials: int = 1, max_steps: int = 12,
              prompt_version: str = "system_v1", make_judge: JudgeFactory | None = None,
              trace_dir: Path | None = None) -> list[TrialScore]:
    scores = []
    with tempfile.TemporaryDirectory(prefix="agentqa-evals-") as tmp:
        for task in tasks:
            for trial in range(trials):
                with trial_world(deployment, scenario, Path(tmp) / f"{task.id}-{trial}.db") as world:
                    apply_setup(world, task)
                    agent = Agent(make_provider(task, trial), prompt_version=prompt_version, max_steps=max_steps)
                    started = time.monotonic()
                    run = agent.run(task.prompt, world)
                    duration = time.monotonic() - started
                    judge = make_judge(task, trial) if make_judge else None
                    score = score_trial(task, trial, run, world, judge)
                    score.duration_s = duration
                    scores.append(score)
                    if trace_dir is not None:
                        write_trace(trace_dir, task, trial, run, score)
    return scores


def write_trace(trace_dir: Path, task: GoldenTask, trial: int, run, score: TrialScore) -> Path:
    """One JSON file per trial: everything needed to see *why* it passed or failed (guide 07)."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{task.id}-{trial}.json"
    path.write_text(json.dumps({
        "task": task.id, "trial": trial, "prompt": task.prompt, "prompt_version": run.prompt_version,
        "provider": run.provider, "outcome": run.outcome.value, "error": run.error,
        "passed": score.passed, "checks": score.checks, "notes": score.notes,
        "duration_s": score.duration_s, "steps": [asdict(s) for s in run.steps],
        "tool_calls": [asdict(c) for c in run.log.calls], "messages": run.messages,
    }, indent=2, default=str))
    return path


def summarise(scores: list[TrialScore]) -> dict:
    counted = [s for s in scores if not s.fallback]
    by_task: dict[str, list[TrialScore]] = defaultdict(list)
    for s in counted:
        by_task[s.task_id].append(s)

    def rate(group: list[TrialScore]) -> dict:
        n, c = len(group), sum(s.passed for s in group)
        low, high = wilson(c, n)
        return {"trials": n, "passed": c, "rate": c / n if n else None, "ci95": [low, high]}

    tasks = {}
    for task_id, group in by_task.items():
        n, c = len(group), sum(s.passed for s in group)
        tasks[task_id] = {
            "category": group[0].category, "trials": n, "passed": c,
            "pass@k": pass_at_k(n, c, n), "pass^k": pass_hat_k(n, c, n),
            "failed_checks": sorted({k for s in group for k, ok in s.checks.items() if not ok}),
            "notes": sorted({note for s in group for note in s.notes}),
        }
    categories = {cat: rate([s for s in counted if s.category == cat])
                  for cat in CATEGORIES if any(s.category == cat for s in counted)}
    outcomes: dict[str, int] = defaultdict(int)
    for s in scores:
        outcomes[s.outcome] += 1
    return {
        "overall": rate(counted),
        "reliable_tasks": sum(1 for t in tasks.values() if t["pass^k"] == 1.0),
        "task_count": len(tasks),
        "categories": categories,
        "tasks": tasks,
        "outcomes": dict(outcomes),
        "fallback_trials_excluded": len(scores) - len(counted),
        "refused_proposals": sum(s.refused_proposals for s in counted),
        "tokens": {"input": sum(s.input_tokens for s in scores), "output": sum(s.output_tokens for s in scores)},
        "judge_tokens": {"input": sum(s.judge_input_tokens for s in scores),
                         "output": sum(s.judge_output_tokens for s in scores)},
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def render_markdown(summary: dict, title: str = "Golden-task eval") -> str:
    o = summary["overall"]
    lines = [
        f"# {title}", "",
        f"**Overall:** {o['passed']}/{o['trials']} trials passed, {_pct(o['rate'])} "
        f"(95% CI {_pct(o['ci95'][0])}–{_pct(o['ci95'][1])}). "
        f"**Reliable tasks** (passed every trial, pass^k = 1): {summary['reliable_tasks']}/{summary['task_count']}.",
        "",
        f"Outcomes: {', '.join(f'{k} {v}' for k, v in sorted(summary['outcomes'].items()))}. "
        f"Fallback-served trials excluded: {summary['fallback_trials_excluded']}. "
        f"Proposals refused by policy: {summary['refused_proposals']}. "
        f"Tokens: {summary['tokens']['input']} in / {summary['tokens']['output']} out"
        + (f"; the judge's: {summary['judge_tokens']['input']} in / {summary['judge_tokens']['output']} out."
           if summary["judge_tokens"]["input"] or summary["judge_tokens"]["output"] else "."),
        "", "| Category | Passed | Rate | 95% CI |", "|---|---|---|---|",
    ]
    for cat, r in summary["categories"].items():
        lines.append(f"| {cat} | {r['passed']}/{r['trials']} | {_pct(r['rate'])} | "
                     f"{_pct(r['ci95'][0])}–{_pct(r['ci95'][1])} |")
    lines += ["", "| Task | Category | Passed | pass@k | pass^k | Failed checks |", "|---|---|---|---|---|---|"]
    for task_id, t in sorted(summary["tasks"].items()):
        lines.append(f"| {task_id} | {t['category']} | {t['passed']}/{t['trials']} | {_pct(t['pass@k'])} | "
                     f"{_pct(t['pass^k'])} | {', '.join(t['failed_checks']) or '–'} |")
    failing = {k: t for k, t in summary["tasks"].items() if t["notes"]}
    if failing:
        lines += ["", "## Why tasks failed", ""]
        for task_id, t in sorted(failing.items()):
            lines.append(f"- **{task_id}:** " + "; ".join(t["notes"]))
    lines += ["", "Rates are per trial. Trials of one task are correlated, so read the category "
              "intervals as optimistic; the per-task pass^k column is the reliability view (guide 04)."]
    return "\n".join(lines) + "\n"


def write_report(scores: list[TrialScore], out_dir: Path, title: str = "Golden-task eval",
                 meta: dict | None = None) -> dict:
    """`meta` (model, prompt, provider, trials) is stored so the gate can price and compare runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarise(scores)
    (out_dir / "results.json").write_text(json.dumps(
        {"meta": meta or {}, "summary": summary,
         "trials": [asdict(s) | {"passed": s.passed} for s in scores]}, indent=2))
    (out_dir / "report.md").write_text(render_markdown(summary, title))
    return summary
