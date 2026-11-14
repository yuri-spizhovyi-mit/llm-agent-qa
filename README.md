# llm-agent-qa

**QA for AI.** An LLM agent that can move money, and the evaluation and red-team suite that
proves it does the right thing and can't be tricked into the wrong one.

- **The agent:** an accounts-payable assistant on the Claude API. It reads invoices, checks
  vendors and balances, and *proposes* payments. Money moves only after human approval,
  through rules enforced in code.
- **Its world:** [`tokenized-settlement-qa`](https://github.com/yuri-spizhovyi-mit/tokenized-settlement-qa)
  v1.0.0 (token, escrow, transfer API, indexer), pinned as a submodule. The agent is judged
  by what happened on the chain, not by what it says.
- **The suite:** golden-task evals with confidence intervals, an LLM judge calibrated against
  human labels, a prompt-injection red team, and a CI gate that blocks a worse prompt or model.

**Status:** milestone 9 of 10: hardening, without API credits (153 tests). The fix for the
agent's main gap (it can't see the payment limit) is ready as prompt `system_v2`, but stays a
candidate until a model has been measured on it.
- **Golden tasks:** Claude (`claude-opus-5-5`) passed 91 of 93 trials (95% CI 92–99%).
- **Red team:** 0 of 64 attack trials moved money. The model attempted 1, and the policy layer
  stopped it.
- **Baseline:** the regression gate's baseline is those real answers, replayed for free in CI.
- **Cost:** $14.77 across five live runs.

The numbers: [`docs/eval-report.md`](docs/eval-report.md). The plan:
[`docs/plan.md`](docs/plan.md). The learning guides: [`docs/learning/`](docs/learning/).

## Run it

```bash
git clone --recurse-submodules https://github.com/yuri-spizhovyi-mit/llm-agent-qa
cd llm-agent-qa
(cd vendor/settlement/contracts && forge build)   # Foundry v1.8.7
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

### Run an eval

```bash
# Replays 93 real Claude answers recorded in a live run (free). --provider live or record needs ANTHROPIC_API_KEY.
PYTHONPATH=src .venv/bin/python -m agentqa.evals --provider replay --trials 3
```

The red team runs the same way. The paid runs kept no red-team transcripts (F-14, fixed since),
so there are no red-team recordings yet: it needs `--provider record` and an API key:

```bash
PYTHONPATH=src .venv/bin/python -m agentqa.evals.redteam --provider record --trials 3
```

Compare a candidate run with the stored baseline (exit code 1 = blocked):

```bash
PYTHONPATH=src .venv/bin/python -m agentqa.evals.gate --baseline evals/baseline/golden.json --candidate reports/golden/results.json
```

Add `--judge` to the golden eval to also grade each answer with the LLM judge. That is refused until a
calibration passes:

```bash
PYTHONPATH=src .venv/bin/python -m agentqa.evals.calibrate --provider record   # needs ANTHROPIC_API_KEY
```

The eval writes `reports/golden/report.md` (pass rates with 95% intervals, pass@k and pass^k per task)
and `results.json`.
