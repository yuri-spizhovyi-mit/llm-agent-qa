"""Build the release evidence pack (learning guide 10).

    python scripts/evidence_pack.py --results results/ --out dist/ --version v1.0.0

`results/` holds the raw outputs of the release run (`.github/workflows/release.yml`):

    junit.xml               pytest --junitxml: the whole suite, no API key
    golden/results.json     python -m agentqa.evals --provider replay --trials 3 --out results/golden
    golden/report.md        (the 93 real Claude answers of run 43140844044, replayed, GATE-D6)

The pack (`dist/evidence-pack-<version>/` and a `.zip` of it) holds those files, the process
documents (plan, requirements, traceability matrix, eval report, findings, decisions, the
learning guides), the baselines, the live runs' own reports (`docs/evidence/`), an `EVIDENCE.md`
summary and `SHA256SUMS` for every file.

It refuses to build (exit 1) if a result file is missing, any test failed or errored, the
traceability has a gap, or the replayed golden run doesn't equal the stored baseline. A pack
exists only for a release that passed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "docs/plan.md", "docs/requirements.csv", "docs/rtm.md", "docs/rtm.csv",
        "docs/eval-report.md", "docs/findings.md", "docs/decisions.md"]
TREES = ["docs/learning", "docs/evidence", "evals/baseline"]
RESULTS = ["junit.xml", "golden/results.json", "golden/report.md"]


def junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    return total


def _timeless(results: dict) -> list[dict]:
    return [{k: v for k, v in t.items() if k != "duration_s"} for t in results["trials"]]


def _rtm():
    spec = importlib.util.spec_from_file_location("rtm", ROOT / "scripts" / "rtm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(results: Path) -> list[str]:
    """Why this run may not become a release; empty when it may."""
    missing = [name for name in RESULTS if not (results / name).exists()]
    if missing:
        return [f"missing result file: {name}" for name in missing]
    out = []
    tests = junit_counts(results / "junit.xml")
    if tests["failures"] or tests["errors"] or not tests["tests"]:
        out.append(f"tests did not pass: {tests['failures']} failed, {tests['errors']} errors of {tests['tests']}")
    out += [f"traceability: {issue}" for issue in _rtm().check()]
    replayed = json.loads((results / "golden" / "results.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "evals" / "baseline" / "golden.json").read_text(encoding="utf-8"))
    if _timeless(replayed) != _timeless(baseline) or replayed["summary"] != baseline["summary"]:
        out.append("the replayed golden run does not equal evals/baseline/golden.json (GATE-D6)")
    return out


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def summary(results: Path, version: str) -> str:
    tests = junit_counts(results / "junit.xml")
    golden = json.loads((results / "golden" / "results.json").read_text(encoding="utf-8"))["summary"]
    red = json.loads((ROOT / "evals" / "baseline" / "redteam.json").read_text(encoding="utf-8"))["summary"]
    rows = _rtm().catalogue()
    g, r = golden["overall"], red["overall"]
    low, high = (round(100 * x) for x in g["ci95"])
    return "\n".join([
        f"# Evidence pack {version}", "",
        f"Commit `{_commit()}`, built {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Every file is listed",
        "with its SHA-256 in `SHA256SUMS`.", "",
        "| Evidence | Result |", "|---|---|",
        f"| Tests (pytest, no API key) | {tests['passed']} passed, {tests['failures']} failed, "
        f"{tests['errors']} errors, {tests['skipped']} skipped |",
        f"| Traceability | {len(rows)} requirements, every one traced (`docs/rtm.md`) |",
        f"| Golden tasks, real Claude answers replayed | {g['passed']}/{g['trials']} trials "
        f"(95% CI {low}–{high}%), {golden['reliable_tasks']}/{golden['task_count']} tasks reliable; "
        "equal to the stored baseline |",
        f"| Red team, live run 43140844044 | {r['executed']}/{r['trials']} executed, "
        f"{r['attempted']}/{r['trials']} attempted |",
        "| Findings | `docs/findings.md`: every finding with its evidence |",
        "| Live runs and cost | `docs/eval-report.md`: five runs, $14.77 |", "",
        "Not measured, waiting for API credits: `judge_v2`'s live calibration, a worse prompt blocked",
        "on the real model, and candidate prompt `system_v2` (decisions GATE-D6, AGT-D4).", ""])


def build(results: Path, out: Path, version: str) -> Path:
    issues = problems(results)
    if issues:
        raise SystemExit("refusing to build the evidence pack:\n- " + "\n- ".join(issues))
    pack = out / f"evidence-pack-{version}"
    if pack.exists():
        shutil.rmtree(pack)
    for name in DOCS:
        (pack / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, pack / name)
    for tree in TREES:
        shutil.copytree(ROOT / tree, pack / tree)
    for name in RESULTS:
        (pack / "results" / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(results / name, pack / "results" / name)
    (pack / "EVIDENCE.md").write_bytes(summary(results, version).encode("utf-8"))
    files = sorted(p for p in pack.rglob("*") if p.is_file())
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(pack).as_posix()}\n" for p in files)
    (pack / "SHA256SUMS").write_bytes(sums.encode("utf-8"))
    return Path(shutil.make_archive(str(pack), "zip", root_dir=out, base_dir=pack.name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    archive = build(args.results, args.out, args.version)
    print(f"wrote {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
