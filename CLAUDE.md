# CLAUDE.md

## What this is

`llm-agent-qa` is **QA for AI**: an LLM agent that can move money (an accounts-payable
assistant on the Claude API), and the evaluation and red-team suite that proves it does
the right thing and can't be tricked into the wrong one. **The suite is the product.**

It is a portfolio and learning project. Every milestone adds a plain-language learning
guide in `docs/learning/`. Roadmap and definition of done: [`docs/plan.md`](docs/plan.md).

The agent's world is `tokenized-settlement-qa` v1.0.0, a git submodule at
`vendor/settlement/` (token, escrow, transfer API, indexer). It is **read-only** here:
never edit files under `vendor/`; a change there is a PR to that repository and a new tag.

## Setup

```bash
git submodule update --init --recursive        # the world, at its pinned tag
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd vendor/settlement/contracts && forge build) # Foundry v1.8.7 (see that repo's CLAUDE.md)
.venv/bin/pytest
```

In a Claude cloud session, `bash vendor/settlement/scripts/cloud-setup.sh` installs the pinned
Foundry.

## Working rules

- One branch per milestone, `feature/<slug>` (slugs in `docs/plan.md`); PR into `main`;
  merge only with green CI. Never skip, disable or loosen a test without a written reason.
- Run the full suite before pushing. Do not push red.
- **CI never needs an API key.** PR checks use the `replay` and `scripted` providers.
  Live model runs happen only in the on-demand live-eval workflow (from M8), with the
  `ANTHROPIC_API_KEY` repository secret. Never put a key in code, logs, fixtures or chat.
- **Safety lives in code, and evals measure it.** A guardrail is never only a sentence in the
  prompt: the policy layer enforces it, deterministic tests prove it, and the evals measure
  how often the model *tries* to break it.
- Default agent model: `claude-opus-5-5`. A different model is a measured comparison
  (M9), never a silent change.
- Data is synthetic. No real people, companies, addresses or invoices.

## Test and eval conventions

- Every test has `@pytest.mark.req("ID")` (prefixes in `docs/plan.md`); module-level
  `pytestmark` is fine. A new requirement goes into `docs/requirements.csv` first; then run
  `python scripts/rtm.py` and commit `docs/rtm.*`. CI fails on an untested requirement, an
  uncatalogued ID or a stale matrix (ENV-05).
- **Score the world, not the words:** an eval checks chain state, the approval queue and the
  tool calls made, before it checks anything the agent *says*.
- **Non-determinism is measured, not hoped away:** an eval result is a rate over repeated runs
  with a confidence interval, never a single pass/fail.
- An LLM judge is used only after its agreement with human labels is measured (M5).
- Findings (planted, real, or bugs in the harness itself) go in `docs/findings.md` with
  evidence. Open behaviour choices go in `docs/decisions.md` with a test.
- Every PR description says what the tests found and explains one test as a teaching example.
