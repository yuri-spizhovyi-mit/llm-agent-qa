# Baselines

The regression gate (`python -m agentqa.evals.gate`) compares a candidate run with the files here:

- `golden.json`: `reports/golden/results.json` from an accepted live run of the golden evals;
- `redteam.json`: `reports/redteam/results.json` from the same accepted run of the red team.

They are recorded in M8, from the first live run, and replaced only on purpose: a new baseline
is a reviewed change, committed with the report that justifies it (decision GATE-D3). Until
they exist, the `eval-gate` workflow runs the evals and uploads the reports, but skips the gate.
