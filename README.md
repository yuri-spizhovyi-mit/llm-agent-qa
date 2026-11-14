# llm-agent-qa

**QA for AI.** An LLM agent that can move money, and the evaluation and red-team suite that
proves it does the right thing and can't be tricked into the wrong one. The suite is the
product.

## The story in two minutes

**The problem.** An agent that pays invoices can be wrong in ways an ordinary test suite
doesn't see. Its outputs are non-deterministic. Its failures are often plausible text, not
exceptions. And anyone who can write an invoice can try to talk it into paying the wrong
account.

**What was built:**
- **An agent:** an accounts-payable assistant on the Claude API (`claude-opus-5-5`). It reads
  invoices, vendors and balances, and *proposes* payments. It can't approve anything: a person
  approves, and a policy layer in code refuses every unsafe payment at proposal and again at
  approval.
- **A real world for it:** [`tokenized-settlement-qa`](https://github.com/yuri-spizhovyi-mit/tokenized-settlement-qa)
  v1.0.0 (token, escrow, transfer API, indexer) on a local chain. Evals score what happened on
  the chain, not what the agent says.
- **The suite:**
  - 31 golden tasks, run repeatedly, reported as rates with confidence intervals;
  - an LLM judge, used only after its agreement with labelled answers is measured;
  - a prompt-injection red team scored at two levels: did the model *attempt* it, and did it
    *execute*;
  - a statistical regression gate with cost and latency budgets.

**What it found** ([`docs/findings.md`](docs/findings.md), 14 findings, each with its evidence):

| | Finding |
|---|---|
| F-06 | **A real hole:** a "re-issued" duplicate invoice got paid twice. Found by the red team, fixed with a new policy rule. |
| F-10 | **The judge passed calibration, then failed 87 of 93 true answers.** Its facts held less than the agent's tools showed. |
| F-13 | **An API outage read as a perfectly safe agent:** "0 of 48 attacks attempted". Now a run that measured nothing fails. |
| F-11 | **The agent can't see the payment limit.** That one gap caused 21 refused proposals, a wrong total and the only attack attempt. |
| F-09 | **Two eval tools crashed on import, and CI called it a pass.** |

**The real numbers** ([`docs/eval-report.md`](docs/eval-report.md), five live runs, $14.77):

| | Result |
|---|---|
| Golden tasks, graded by code | **91/93** trials (95% CI 92–99%), 30 of 31 tasks every time |
| Red team | **0 of 64** attacks executed; 1 attempted, and the policy stopped it |
| Legitimate look-alikes refused | 0 of 24 |

**How to check it:**
- **The baseline is replayable.** The 93 real Claude answers are recorded, and CI replays them
  on every PR for free. The result must equal the stored baseline.
- **Every requirement has a test.** The 43 requirements are traced to their tests by a matrix
  generated from the tests themselves ([`docs/rtm.md`](docs/rtm.md)).
- **Each release carries its evidence.** The release has an evidence pack with SHA-256 sums for
  every file.

**Honest limits.** Three things wait for API credits: the second judge's live calibration, a
worse prompt shown blocked on the real model, and the candidate fix for F-11 (`system_v2`). They
are listed in decisions GATE-D6 and AGT-D4, not hidden.

## How it fits together

```text
operator request ─▶ agent loop (Claude API) ─▶ tools ─▶ policy layer ─▶ approval queue ─▶ person approves
                         ▲                                  │ refuses unsafe proposals      │
                         └──── tool results, refusals ◀─────┘                               ▼
                                                                    transfer API ─▶ chain (Anvil)
evals: golden tasks, red team, judge ──▶ results.json ──▶ regression gate vs evals/baseline/
```

| Where | What |
|---|---|
| [`src/agentqa/`](src/agentqa/) | the agent loop, tools, policy layer, providers (live / record / replay / scripted), judge |
| [`src/agentqa/evals/`](src/agentqa/evals/) | golden runner and scorers, red team, judge calibration, gate, statistics |
| [`evals/`](evals/) | tasks, attacks, judge labels, the baselines, and the real recordings |
| [`tests/`](tests/) | 157 tests, all without an API key |
| [`docs/`](docs/) | plan, requirements and traceability, eval report, findings, decisions, evidence |
| [`docs/learning/`](docs/learning/) | ten learning guides, one per milestone, with interview questions |

## Run it

```bash
git clone --recurse-submodules https://github.com/yuri-spizhovyi-mit/llm-agent-qa
cd llm-agent-qa
(cd vendor/settlement/contracts && forge build)   # Foundry v1.8.7
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

Replay the 93 real Claude answers (free) and compare them with the baseline:

```bash
PYTHONPATH=src .venv/bin/python -m agentqa.evals --provider replay --trials 3
```

```bash
PYTHONPATH=src .venv/bin/python -m agentqa.evals.gate --baseline evals/baseline/golden.json --candidate reports/golden/results.json
```

The traceability check, and the evidence pack (from a release run's results):

```bash
python scripts/rtm.py --check
```

```bash
python scripts/evidence_pack.py --results results --out dist --version v1.0.0
```

Live runs (`--provider live` or `record`, on the golden evals, the red team
`agentqa.evals.redteam` and the judge's calibration `agentqa.evals.calibrate`) need
`ANTHROPIC_API_KEY` and cost money. In CI they run only in the on-demand `eval-gate` workflow.

## The learning guides

1. [Agents, and why QA for AI is different](docs/learning/01-agents-and-qa-for-ai.md)
2. [Tools and safe tool design](docs/learning/02-tools-and-safe-tool-design.md)
3. [The agent loop and defence in depth](docs/learning/03-the-agent-loop-and-defence-in-depth.md)
4. [Evals 101](docs/learning/04-evals-101.md)
5. [LLM-as-judge](docs/learning/05-llm-as-judge.md)
6. [Prompt injection and red-teaming](docs/learning/06-prompt-injection-and-red-teaming.md)
7. [Regression gates, budgets and observability](docs/learning/07-regression-gates-budgets-observability.md)
8. [Running evals for real](docs/learning/08-running-evals-for-real.md)
9. [Improving an agent with evals](docs/learning/09-improving-an-agent-with-evals.md)
10. [Presenting AI-QA work](docs/learning/10-presenting-ai-qa-work.md)
