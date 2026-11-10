# 07 · Regression testing for AI: gates, budgets and traces

*Milestone 7. Code: `src/agentqa/evals/gate.py`, `stats.py`, `evals/gate.json`,
`.github/workflows/eval-gate.yml`. Tests: GATE-01..05.*

## 1. Why AI needs a regression gate

In ordinary software, a change that breaks something usually turns a test red. With an agent,
the riskiest changes touch **no code at all**:

- a reworded system prompt ("be more helpful" can mean "be less careful");
- a changed tool description;
- a new model version, or a cheaper model;
- a different effort setting.

Each can shift behaviour across many tasks at once, slightly and unpredictably. So every such
change must be **measured against a baseline** before it merges. Any other gate is a guess.

## 2. The problem: noise

Run the same agent twice and you get different numbers. Our flaky test agent fails 5% of trials
at random. With two seeds it fails *different* trials, so some tasks look "worse" and some
"better" with nothing changed at all (GATE-02's neutral test checks this happens).

So the question is never "is the number lower?" It is "**is it lower by more than noise
explains?**" That's a statistical test.

## 3. The tests the gate uses (GATE-D1)

**Primary: the paired sign test over tasks.** For each task, compare the candidate's pass rate
with the baseline's: worse, better, or unchanged. Ignore the unchanged tasks. If nothing really
changed, "worse" and "better" are a coin flip, 50/50. If 16 tasks got worse and none better, the
chance of that under a fair coin is 0.5¹⁶ ≈ 0.00002. So: blocked.

*Why pair by task?* The trials of one task aren't independent; some tasks are simply hard. Treating
93 correlated trials as 93 independent ones overstates the evidence. Comparing task by task
uses each task as its own control.

**Secondary: Fisher's exact test on pooled trials.** Take baseline passed/failed and candidate
passed/failed as a 2×2 table. Fisher asks how likely the candidate's low count is if both came
from the same rate. It's exact, so it is valid at small sample sizes, where the usual z-test
isn't. It catches a broad, shallow drop: every task a little worse, no task clearly so.

Both are **one-sided**: we only block on *worse*. Both use **α = 0.05**, so about 1 neutral change
in 20 is blocked by bad luck per test. The policy is to re-run once: a real regression blocks
again, and noise usually doesn't.

GATE-01 checks both against hand-computed values. The classic Fisher case is 10/10 → 5/10,
p = C(15,5)/C(20,10) = 0.016.

## 4. Budgets: cost and latency are test criteria

A prompt that is 2% better and 3× more expensive is probably a regression. The gate prices every
trial from its token counts and the model's price table (`evals/gate.json`):

```text
cost per trial = (input tokens × input price + output tokens × output price) / 1,000,000
```

It blocks if the mean is over **$0.40 per trial**, or more than **1.25× the baseline's**.
Fallback-served trials are not priced, because they ran on a different model.

Latency is gated on the **95th percentile**, not the mean. A mean hides a slow tail: 10% of runs
taking 2.5 minutes is a real problem that a mean of 42 s conceals. A max over-reacts: two
stragglers in 93 shouldn't block a release. F-08 was a test that couldn't tell these apart,
because every trial took the same time. Test inputs must be ones where the right and wrong
statistics *disagree*.

## 5. The red team is part of the gate

- **Any executed attack blocks**, whatever the statistics. Some failures have no acceptable rate.
- **Significantly more attempts blocks:** the candidate is easier to steer. This is the safety
  margin shrinking, even if the guardrails still hold.
- **Significantly more over-blocking blocks:** the candidate got "safe" by refusing work.

GATE-04 proves all three with the M6 scripted agents. Careful vs careful passes. Careful vs
gullible blocks on attempts *only*. Careful vs paranoid blocks on over-blocking *only*.

## 6. Observability: traces

A blocked gate says *that* something got worse. A trace says *why*. With `--traces DIR`, every
trial writes one JSON file containing:
- the full conversation (every message, unchanged);
- every tool call, refused ones included;
- each step's stop reason, the model that served it, and its tokens;
- every check and the notes on why it failed;
- the duration.

Debugging an agent is mostly reading traces: "on G-HOLD-04 the new prompt proposed INV-107,
because it read 'annual licence' as urgent". The live workflow uploads them as an artifact.

## 7. When the gate runs (GATE-D2)

| Trigger | Runs |
|---|---|
| every PR (`ci`) | pytest with scripted and replay providers: free, deterministic, no key |
| a PR that changes `prompts/**` or `tools.py` (`eval-gate`) | live golden + red team + gate (only if the key secret exists) |
| manual (`workflow_dispatch`) | the same, with a chosen prompt version and trial count |

The baseline changes only through a reviewed commit (GATE-D3). Otherwise slow regressions slip
in one "neutral" step at a time.

## 8. What's proven now, and what M8 adds

Proven here, with scripted candidates (124 tests):
- a worse candidate is blocked, a neutral one passes, and a broadly flakier one is blocked;
- skipped tasks, cost growth, slow tails, executed attacks, more steering and more refusing are
  each blocked by their own check.

M8 adds the real thing: a live baseline, then a deliberately worse prompt variant run through
the same workflow and blocked.

## 9. Interview questions

**"How do you stop a prompt change from making your agent worse?"**
> A regression gate. Every prompt, tool or model change runs the eval suite and is compared with
> a stored baseline. Because outputs are noisy, I use a statistical test, not a threshold. Mine
> is a paired sign test over tasks, backed by Fisher's exact test on pooled trials. Cost per
> task and p95 latency are budgets in the same gate, and any executed red-team attack blocks
> regardless.

**"Why not just compare the average score?"**
> Because two runs of the same agent give different averages. I need to know if the drop is
> bigger than noise. And trials of one task are correlated, so I pair by task rather than
> pretend every trial is independent.

**"How do you debug an agent regression?"**
> Traces. Every trial records the full conversation, every tool call including refused ones,
> each step's model and tokens, and why each check failed. I diff the failing task's trace
> between baseline and candidate.

**"Isn't running evals on every PR expensive?"**
> Yes, so the live gate runs only when what the model sees changes: prompts or tool
> definitions. Everything else runs on recorded and scripted responses, which are free.
