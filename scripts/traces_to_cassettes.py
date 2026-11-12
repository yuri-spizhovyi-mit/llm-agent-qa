"""Turns golden-eval traces from a live run into replay cassettes (learning guide 08, GATE-D6).

A live run's traces (`--traces`) keep every message of every trial. Each assistant message is
one model response, and the request that produced it is the conversation before it, so the
recording can be rebuilt without calling the API again: this is how the paid live run
43140844044 became the free, replayable baseline in `evals/cassettes/golden/`.

The request key also hashes the system prompt and the tool definitions, taken from the code at
hand: a cassette replays only while those are what the model saw when it answered. A trace keeps
part of each response (content, stop reason, serving model, token counts), which is everything
the agent loop reads, except two things: a fallback marker and a refusal's details. A trace with
a fallback-served step or a refusal is refused here, rather than replayed as something else.

usage: python scripts/traces_to_cassettes.py TRACE_DIR OUT_DIR [--model MODEL] [TRACE_NAME ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentqa.agent import load_prompt  # noqa: E402
from agentqa.providers import DEFAULT_MODEL, Cassette, request_key  # noqa: E402
from agentqa.tools import definitions  # noqa: E402


def cassette_from_trace(trace: dict, *, model: str, tools: list[dict]) -> Cassette:
    system = load_prompt(trace["prompt_version"])
    messages, steps = trace["messages"], trace["steps"]
    answers = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    if len(answers) != len(steps):
        raise ValueError(f"{len(answers)} assistant messages but {len(steps)} steps: not a whole trace")
    cassette = Cassette(model=model)
    for i, step in zip(answers, steps):
        if step["fallback"] or step["stop_reason"] == "refusal":
            raise ValueError(f"step {len(cassette.entries)} was fallback-served or a refusal: "
                             "its trace lacks what the agent reads from such a response")
        key = request_key(model=model, system=system, messages=messages[:i], tools=tools)
        cassette.entries[key] = {"model": step["served_by"], "stop_reason": step["stop_reason"],
                                 "stop_details": None, "content": messages[i]["content"],
                                 "usage": {"input_tokens": step["input_tokens"],
                                           "output_tokens": step["output_tokens"]}}
    return cassette


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("traces", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("names", nargs="*", help="trace names, e.g. G-PAY-01-0 (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="the model the run requested")
    args = parser.parse_args(argv)

    names = args.names or [p.stem for p in sorted(args.traces.glob("*.json"))]
    tools = definitions()
    for name in names:
        trace = json.loads((args.traces / f"{name}.json").read_text(encoding="utf-8"))
        try:
            cassette_from_trace(trace, model=args.model, tools=tools).save(args.out / f"{name}.json")
        except ValueError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
    print(f"wrote {len(names)} cassettes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
