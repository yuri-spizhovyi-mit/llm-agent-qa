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

**Status:** milestone 1 of 10. See [`docs/plan.md`](docs/plan.md) and the learning guides in
[`docs/learning/`](docs/learning/).

## Run it

```bash
git clone --recurse-submodules https://github.com/yuri-spizhovyi-mit/llm-agent-qa
cd llm-agent-qa
(cd vendor/settlement/contracts && forge build)   # Foundry v1.8.7
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```
