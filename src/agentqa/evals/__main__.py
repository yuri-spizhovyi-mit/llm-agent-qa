"""Run an eval suite for real: `python -m agentqa.evals [options]`.

    --provider live     the real Claude API (needs ANTHROPIC_API_KEY; costs money; M8)
    --provider record   live, and save every response as a cassette under --cassettes
    --provider replay   replay saved cassettes (free; fails on any request not recorded)

Starts its own Anvil, deploys the world, runs tasks × trials in isolation, and writes
`report.md` and `results.json` to --out. CI never runs this with a live provider (ENV-D2).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "vendor/settlement/tests"), str(ROOT / "vendor/settlement/services")]

from web3 import Web3  # noqa: E402

from lib import artifacts  # noqa: E402
from lib.anvil import Anvil  # noqa: E402
from lib.chain import Chain  # noqa: E402

from ..providers import DEFAULT_MODEL, Cassette, LiveProvider, RecordingProvider, ReplayProvider  # noqa: E402
from ..scenarios import base_scenario  # noqa: E402
from ..world import Deployment  # noqa: E402
from . import dataset, runner  # noqa: E402


class _SavingRecorder(RecordingProvider):
    """Saves the cassette after every response, so an interrupted paid run keeps what it got."""

    def __init__(self, inner, cassette, path):
        super().__init__(inner, cassette)
        self.path = path

    def create(self, **kwargs):
        response = super().create(**kwargs)
        self.cassette.save(self.path)
        return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentqa.evals", description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", choices=["live", "record", "replay"], required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default="system_v1")
    parser.add_argument("--cassettes", type=Path, default=ROOT / "evals" / "cassettes" / "golden")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "golden")
    args = parser.parse_args(argv)

    tasks = dataset.load_tasks()
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in wanted]
    scenario = base_scenario()
    dataset.validate(tasks, {i.invoice_id for i in scenario.invoices}, {v.vendor_id for v in scenario.vendors})

    if args.provider == "replay":
        missing = [f"{t.id}-{i}.json" for t in tasks for i in range(args.trials)
                   if not (args.cassettes / f"{t.id}-{i}.json").exists()]
        if missing:
            print(f"no cassette for {len(missing)} trial(s) in {args.cassettes}: {', '.join(missing[:5])}"
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

    artifacts.ensure_built()
    node = Anvil.start()
    try:
        chain = Chain(Web3(Web3.HTTPProvider(node.url, request_kwargs={"timeout": 30})))
        deployment = Deployment.deploy(chain, scenario)
        scores = runner.run_suite(tasks, make_provider, deployment, scenario,
                                  trials=args.trials, prompt_version=args.prompt)
    finally:
        node.stop()
    title = f"Golden-task eval: {args.model}, prompt {args.prompt}, {args.provider}, {args.trials} trials"
    summary = runner.write_report(scores, args.out, title)
    print((args.out / "report.md").read_text())
    return 0 if summary["overall"]["passed"] == summary["overall"]["trials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
