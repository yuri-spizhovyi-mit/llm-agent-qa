# Baselines

The regression gate (`python -m agentqa.evals.gate`) compares a candidate run with the files here.
They are replaced only on purpose: a new baseline is a reviewed change, committed with the report
that justifies it (decision GATE-D3).

## The current baseline (M8, decision GATE-D6)

`claude-opus-5-5`, prompt `system_v1`, 3 trials, from live run
[43140844044](https://github.com/yuri-spizhovyi-mit/llm-agent-qa/actions/runs/43140844044):

- **`golden.json`: 91/93, graded by code only (`meta.judge: null`).** The run's 93 real responses
  are kept as cassettes in `../cassettes/golden/`. They were rebuilt from the run's traces by
  `scripts/traces_to_cassettes.py`, and this file is what replaying them produces:

  ```bash
  PYTHONPATH=src python -m agentqa.evals --provider replay --trials 3 --out reports/golden
  cp reports/golden/results.json evals/baseline/golden.json
  ```

  CI replays them on every PR and checks that the result equals this file, durations aside
  (GATE-05). Its `duration_s` values are replay times, not latencies: the gate checks only the
  candidate's.
- **`redteam.json`: 0/48 attacks attempted, 0 executed, 0/18 look-alikes failed.** It is the
  same run's live results file, as written. The run kept no red-team traces (F-14), so it can't
  be replayed.
- **No `calibration.json`.** Code graded this baseline, not an LLM judge, so there is no
  calibration to show. The run's `judge_v1` calibration is kept as evidence in
  `docs/evidence/run-43140844044/judge/`. It was retired after F-10.

A candidate is comparable with this baseline only if it is also graded without a judge
(GATE-D4): run the golden evals without `--judge`. The first judged run after `judge_v2`'s live
calibration becomes the next baseline candidate instead.
