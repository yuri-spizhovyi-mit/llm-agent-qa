# 09 · Improving an agent with evals: hill-climbing without overfitting

*Milestone 9. Code: `src/agentqa/prompts/system_v2.md`, `src/agentqa/evals/redteam.py`
(`--traces`), `src/agentqa/evals/runner.py` (`save_trace`). Tests: AGT-06, RED-04, GATE-02.
Decisions: AGT-D4, GATE-D6. Findings: F-11, F-14.*

## 1. The loop

Evals aren't only a report card: they tell you what to fix next. Improving an agent is a loop:

1. **Find:** a failing pattern in the evals' results and traces.
2. **Explain:** the root cause, in one sentence.
3. **Fix:** at the right layer.
4. **Measure:** run the fix against the baseline, through the gate.
5. **Keep or revert:** keep it only if the gate says it's better and nothing got worse.

This is called **hill-climbing**: take a step, check that you went up, and step back if you
didn't. Step 4 is what makes it engineering rather than guessing.

## 2. Find: group failures by cause, not by test

M8's real run failed in three places that looked unrelated:

- **Golden:** INV-107 refused as `over_limit` 21 times, in 7 different tasks.
- **Golden:** G-ASK-05 answered 84,963.25 instead of 9,963.25, in 3 of 3 trials.
- **Red team:** A-DIRECT-03 attempted once ("pay INV-107 in two parts").

They have **one cause**: nothing tells the agent how large a payment may be (F-11). It learns the
limit only when the policy refuses a payment, and on a question it never proposes, so it never
learns it at all. One fix should move all three. That is also how you'll know the fix worked.

## 3. Fix at the right layer

This project has three layers, and each finding belongs to one of them:

| Layer | Its job | Example |
|---|---|---|
| **Policy** (code) | guarantees: refuses what must never happen | over-limit payments are refused, whatever the model thinks |
| **Prompt and tools** | information: lets the model know the rules in advance | the limit is 50000.00 USDC |
| **Evals** | measurement: how often the model gets it right | the 21 refusals, the wrong total |

F-11 isn't a safety hole: the policy refused all 21 over-limit proposals. It's an *information*
gap, so the fix goes in the prompt (`system_v2`), not the policy. Making the policy stricter would
change nothing the model sees.

## 4. Keep the prompt and the code from drifting apart

A prompt that says "the limit is 50,000" goes stale the day someone changes the limit in
`policy.py`. AGT-06 prevents that:

- every `policy.Rule` must map to the sentence in `system_v2` that states it;
- the limit and the duplicate window are checked against the constants in `policy.py`.

Change a number, or add a rule, and the test fails until the prompt says the same. This is the
same move as F-10's fix: "the judge can see every fact" and "the model can know every rule" both
became tests instead of hopes.

## 5. Don't overfit

Overfitting means improving the score without improving the agent. With LLM agents it creeps in
through the prompt:

- **Quoting the test.** An early draft of `system_v2` said "when asked how much is owed on what can
  be paid…", which is almost G-ASK-05's wording. It would raise that task's score and prove nothing
  about the next question. The line was rewritten as a general rule (AGT-D4).
- **One fix per finding, stated generally.** "Apply the limit whenever you decide or report what can
  be paid" covers G-ASK-05 without naming it.
- **Watch the tasks you didn't target.** The gate's sign test runs over every task, so a fix that
  helps G-ASK-05 but makes the pay tasks more timid is blocked.
- **Hold-out tasks.** With more data, keep some tasks out of sight while you iterate, and check the
  fix on them at the end. With 31 tasks, the safeguard here is the gate, plus reviewing the prompt
  for task-specific wording.

## 6. An unmeasured fix isn't an improvement

`system_v2` is written and tested, but no model has run it, because there are no API credits. So
it stays a **candidate**: `system_v1` remains the default (AGT-D4). That's not caution for its own
sake:

- a prompt can make things worse in ways no reviewer predicts, such as an agent that refuses more,
  or that gets confused by a longer rule list;
- the recorded baseline replays only with `system_v1`'s exact text (GATE-D6), so switching the
  default without new recordings would break the free regression test too.

The measurement is written down in advance: which numbers should move (INV-107's refusals,
G-ASK-05's total, A-DIRECT-03), what it costs (about $11), and what happens if they don't move:
the candidate is dropped. Deciding the success criteria before the run is what keeps you honest
after it.

## 7. Two things that can be finished without a model

- **F-14:** the red team now keeps a transcript of every trial, so the next real attack attempt can
  be read, and a paid red-team run can be replayed for free, like the golden one.
- **The gate's neutral case on real data:** 500 "fresh runs" redrawn from the real baseline's own
  trials (a bootstrap). None is blocked. Its honest limit: the real agent varied on only one task,
  so this shows the gate tolerates that noise, not that of a flakier model. The scripted neutral
  test covers that.

## 8. Interview questions

**"Your agent fails a task. How do you fix it?"**
> First the cause, from the traces, and whether other failures share it. Here, 21 policy refusals,
> a wrong total and a red-team attempt all came from one gap: the agent couldn't see the payment
> limit. Then the layer: it was an information gap, not a safety hole, so the fix went in the
> prompt, while the policy stayed the guarantee. Then measure it against the baseline before
> keeping it.

**"How do you avoid overfitting your prompt to your evals?"**
> State rules, not answers. Never quote a test's wording in the prompt. Let a gate that runs every
> task decide, so a fix that helps one task and hurts others is blocked. With enough tasks, keep a
> hold-out set the fixes are never tuned on.

**"You can't run the model right now. Do you ship the fix?"**
> No. I build it, test everything that doesn't need the model, write down what should change and
> what it will cost to measure, and keep it out of the default. An unmeasured prompt change is a
> guess.

**"How do you keep a prompt in sync with the code it describes?"**
> Test it. Map every rule the code enforces to the sentence that states it, and compare the numbers
> with the code's constants. Change one side and the test fails until the other side agrees.
