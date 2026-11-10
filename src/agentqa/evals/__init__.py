"""Evals: datasets of tasks, scorers on outcomes, repeated trials, statistics, reports.

Learning guide 04 explains the ideas. The pieces:
- `dataset.py`: golden tasks (JSON), each with optional setup operations on the world;
- `scoring.py`: per-trial checks on what happened (proposals, chain, tools, answer);
- `stats.py`: Wilson intervals, pass@k and pass^k;
- `runner.py`: isolated trials (chain snapshot + fresh books per trial) and the report;
- `__main__.py`: `python -m agentqa.evals` for real runs (live or replay).
"""
