"""Run an eval suite for real: `python -m agentqa.evals [options]`.

    --provider live     the real Claude API (needs ANTHROPIC_API_KEY; costs money; M8)
    --provider record   live, and save every response as a cassette under --cassettes
    --provider replay   replay saved cassettes (free; fails on any request not recorded)
    --judge             also grade each answer with the LLM judge, using the same provider mode.
                        Refused unless reports/judge/calibration.json says the judge is trusted
                        for this judge prompt and model (JDG-D2).

Starts its own Anvil, deploys the world, runs tasks × trials in isolation, and writes
`report.md` and `results.json` to --out. CI never runs this with a live provider (ENV-D2).
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

from .. import vendor

vendor.ensure_on_path()  # before `lib` below (F-09)

ROOT = Path(__file__).resolve().parents[3]

from web3 import Web3  # noqa: E402

from lib import artifacts  # noqa: E402
from lib.anvil import Anvil  # noqa: E402
from lib.chain import Chain  # noqa: E402

from ..judge import JUDGE_PROMPT, Judge, LiveJudgeProvider  # noqa: E402
from ..providers import DEFAULT_MODEL, Cassette, LiveProvider, RecordingProvider, ReplayProvider  # noqa: E402
from ..scenarios import base_scenario  # noqa: E402
from ..world import Deployment  # noqa: E402
from . import dataset, runner  # noqa: E402
from .calibrate import require_trusted  # noqa: E402


class _SavingRecorder(RecordingProvider):
    """Saves the cassette after every response, so an interrupted paid run keeps what it got."""

    def __init__(self, inner, cassette, path):
        super().__init__(inner, cassette)
        self.path = path

    def create(self, **kwargs):
        response = super().create(**kwargs)
        self.cassette.save(self.path)
        return response


@contextmanager
def started_world(scenario):
    """Its own Anvil plus a deployment of the world; the node stops afterwards."""
    artifacts.ensure_built()
    node = Anvil.start()
    try:
        chain = Chain(Web3(Web3.HTTPProvider(node.url, request_kwargs={"timeout": 30})))
        yield Deployment.deploy(chain, scenario)
    finally:
        node.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentqa.evals", description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", choices=["live", "record", "replay"], required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default="system_v1")
    parser.add_argument("--cassettes", type=Path, default=ROOT / "evals" / "cassettes" / "golden")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "golden")
    parser.add_argument("--traces", type=Path, default=None, help="write one JSON trace per trial here")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--judge-calibration", type=Path, default=ROOT / "reports" / "judge" / "calibration.json")
    parser.add_argument("--judge-cassettes", type=Path, default=ROOT / "evals" / "cassettes" / "judge-golden")
    args = parser.parse_args(argv)

    tasks = dataset.load_tasks()
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in wanted]
    scenario = base_scenario()
    dataset.validate(tasks, {i.invoice_id for i in scenario.invoices}, {v.vendor_id for v in scenario.vendors})

    if args.provider == "replay":
        dirs = [args.cassettes] + ([args.judge_cassettes] if args.judge else [])
        missing = [str(d / f"{t.id}-{i}.json") for d in dirs for t in tasks for i in range(args.trials)
                   if not (d / f"{t.id}-{i}.json").exists()]
        if missing:
            print(f"no cassette for {len(missing)} trial(s): {', '.join(missing[:5])}"
                  f"{' ...' if len(missing) > 5 else ''}. Record them with --provider record.", file=sys.stderr)
            return 2

    live = LiveProvider(model=args.model) if args.provider in ("live", "record") else None

    def make_provider(task, trial):
        path = args.cassettes / f"{task.id}-{trial}.json"
        if args.provider == "live":
            return live
        if args.provider == "record":
            return _SavingRecorder(live, Cassette(model=args.model), path)
        return ReplayProvider(Cassette.load(path))

    make_judge = None
    if args.judge:
        require_trusted(args.judge_calibration, judge_prompt=JUDGE_PROMPT, model=args.model)
        live_judge = LiveJudgeProvider(model=args.model) if args.provider in ("live", "record") else None

        def judge_for(task, trial):
            path = args.judge_cassettes / f"{task.id}-{trial}.json"
            if args.provider == "live":
                return Judge(live_judge)
            if args.provider == "record":
                return Judge(_SavingRecorder(live_judge, Cassette(model=args.model), path))
            return Judge(ReplayProvider(Cassette.load(path)))
        make_judge = judge_for

    with started_world(scenario) as deployment:
        scores = runner.run_suite(tasks, make_provider, deployment, scenario,
                                  trials=args.trials, prompt_version=args.prompt, make_judge=make_judge,
                                  trace_dir=args.traces)
    title = f"Golden-task eval: {args.model}, prompt {args.prompt}, {args.provider}, {args.trials} trials"
    # Which judge graded the run, not just whether one did: the gate compares only runs graded
    # by the same judge (GATE-D4).
    judge = {"prompt": JUDGE_PROMPT, "model": args.model} if args.judge else None
    summary = runner.write_report(scores, args.out, title, meta={
        "model": args.model, "prompt": args.prompt, "provider": args.provider, "trials": args.trials,
        "judge": judge})
    print((args.out / "report.md").read_text())
    return 0 if summary["overall"]["passed"] == summary["overall"]["trials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
