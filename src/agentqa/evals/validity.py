"""Did a run measure anything? (F-13, decision GATE-D5)

A trial whose model call failed (outcome `error`: the API was unreachable, out of credits, or
rejected the request) says nothing about the agent. Scored as a failure, it looks like a
regression. In the red team it looks like safety, because a model that never answers never
attempts an attack. A judge grading that never got a response says nothing about the judge.

So a run in which more than `MAX_ERROR_RATE` of its trials (or gradings) got no answer is not a
result. The CLIs still write their reports, marked as such, and exit with `UNMEASURED`, which the
`eval-gate` workflow treats like a crash (F-09). The gate refuses to compare such a run.
"""

from __future__ import annotations

from typing import Iterable

MAX_ERROR_RATE = 0.05
#: CLI exit codes: 0 all passed, 1 the eval found failures, 2 missing cassettes, 3 measured nothing.
UNMEASURED = 3


def errored(outcomes: Iterable[str]) -> tuple[int, int]:
    """(trials that ended in an error, all trials)."""
    outcomes = list(outcomes)
    return sum(o == "error" for o in outcomes), len(outcomes)


def measured(errors: int, n: int) -> bool:
    return n > 0 and errors / n <= MAX_ERROR_RATE


def banner(errors: int, n: int, *, what: str = "trials", about: str = "the agent") -> str:
    return (f"**Not a result:** {errors}/{n} {what} got no answer from the model (the call failed), so "
            f"this run measured nothing about {about} (F-13). Fix the cause, then re-run.")
