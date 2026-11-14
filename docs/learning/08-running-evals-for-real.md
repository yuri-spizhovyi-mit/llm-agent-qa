# 08 · Running evals for real: reading results honestly

*Milestone 8. Code: `.github/workflows/eval-gate.yml`, `src/agentqa/evals/validity.py`,
`judge.render_facts`, `scripts/traces_to_cassettes.py`, `evals/baseline/`,
`evals/cassettes/golden/`. Results: `docs/eval-report.md`. Findings: F-09..F-14.*

## 1. What changes when the model is real

Until M8, every test ran against scripted models: free, instant and deterministic. A real
model adds four things a test suite has to survive:

- **Cost:** every trial is a bill. A full pass here (calibration, 93 golden trials, 66 red-team
  trials) cost about $10.
- **Noise:** the same task can pass and then fail. Results are rates with intervals (guide 04).
- **Outages:** the API can refuse to answer, for example when credits run out.
- **A second model in the loop:** the LLM judge is a model too, and it can be wrong in ways
  calibration didn't catch.

The rule for this milestone: **before you believe a number, prove the run could have produced
a different one.**

## 2. The first real numbers

| | Result |
|---|---|
| Golden tasks, graded by code | 91/93 (95% CI 92–99%), 30 of 31 tasks every time |
| Red team, executed | 0 of 64 attack trials (must be 0) |
| Red team, attempted | 1 of 64 (0.3–8%): the policy stopped it |
| Legitimate look-alikes failed | 0 of 24 |
| Cost | $14.77 across five runs |

The details are in `docs/eval-report.md`. The rest of this guide is about the numbers that were
*wrong*, because that's where the lessons are.

## 3. A number that's too bad: read the traces first (F-10)

The first 3-trial run graded the golden tasks with the LLM judge: **6 of 93 passed**. The judge
had passed calibration (kappa 0.90), so the easy conclusion was "the agent is bad".

The traces said otherwise. Each failure said the agent "added facts": `PAY-001`, a vendor ID, a
payout address. All of them were true, and the agent had read them from its own tools. The
judge's reference facts simply didn't contain them. The judge applied its rules correctly to an
incomplete truth.

- **The fix:** the judge sees every value any tool can show (`judge.render_facts`). A test now
  asks the tools for every value and checks each one is in the facts, so the gap can't come back.
- **Why calibration missed it:** the calibration answers were short and written by hand. Real
  answers are long and full of IDs. A judge is calibrated only for the kind of answers it was
  shown, so the calibration set now includes real ones.

**Lesson:** a surprising number is a hypothesis about the *measurement* first. Read 3–5 failing
traces before you believe a bad rate, or a good one.

## 4. A number that's too good: did anything run? (F-09, F-13)

Two runs went green while measuring nothing:

- **F-09:** two eval CLIs crashed on import, and the workflow's `|| true` swallowed the crash.
- **F-13:** every API call failed for lack of credits. The red team reported **0/48 attacks
  attempted**, which reads like a perfectly safe agent. A model that never answers never attempts
  anything.

The fix is a rule, not a patch: "it found problems" and "it didn't run" must never share an exit
path. The CLIs now exit 0 (all passed), 1 (found failures), 2 (a cassette is missing) or 3
(measured nothing). The workflow fails the job on a crash or a 3. The gate refuses to compare a
run in which more than 5% of trials got no answer (`validity.py`, GATE-D5).

**Lesson:** "no bad events" is evidence only if the system could have produced them.

## 5. Record once, replay forever

A paid run is too expensive to throw away. The golden eval writes a trace per trial: every
message, every tool call, each step's model and tokens. Those traces hold everything needed to
rebuild the recordings:

- each assistant message is one model response;
- the request that produced it is the conversation before it;
- so `scripts/traces_to_cassettes.py` can key each response by the hash of that request, exactly
  as the recording provider would have.

The 93 trials of run 43140844044 became `evals/cassettes/golden/`, 656 KB. Replaying them takes
18 seconds, costs nothing, and matches the live run on every trial: outcome, tool calls, refused
proposals, tokens, and every check made by code.

What a replay **can't** reproduce:
- **Latency:** a replay answers in milliseconds, so latency comes from the live run;
- **A different prompt or tool:** the request hash covers the system prompt and the tool
  definitions. Change either and every recording misses, which is the point: the old answers
  belong to a question nobody asks any more.

## 6. The baseline, and comparing like with like

The gate needs a baseline it can measure against (guide 07). Graded by the `judge_v1` judge, the
run was useless as one: at 6/93, no task could get worse. Graded by code, it was 91/93. That's
what `evals/baseline/golden.json` holds, and CI replays the recordings on every PR to prove the
file is exactly what they produce (GATE-D6).

**A judged run and an unjudged run are measured with different rulers.** The judge adds a check
to every trial, so a judged candidate looks worse than an unjudged baseline for reasons that
have nothing to do with the agent. The gate's first check, `comparable`, refuses that
comparison (GATE-D4).

## 7. Count every call (F-12)

The first full run's reports covered $6.94 of a roughly $10 bill: the red team's attack trials
and the judge's gradings recorded no tokens. Every call is recorded now. The check that matters
is the one done here: add up the runs' estimates (about $14.30) and compare them with what the
API console says ($14.77). Close numbers mean nothing large is missing.

## 8. Small samples, honestly

- **0 of 48 isn't zero.** With 95% confidence, the true attempt rate is anywhere from 0 to 7%.
  Report the interval, not the zero.
- **3 trials per task is few.** One failure moves a task by 33 points, and "passed all 3" is weak
  evidence of reliability.
- **The one real attempt** (A-DIRECT-03) and both golden failures have one cause: the agent
  can't see the payment limit's value (F-11). Group failures by cause, not by test.

## 9. What's proven, and what waits

**Proven:**
- the pipeline runs live end to end;
- crashes and outages fail loudly;
- the judge's facts are complete;
- the baseline is real and reproducible;
- the gate blocks scripted worse candidates against it.

**Waiting for API credits:** `judge_v2`'s live calibration, a real worse prompt blocked by the
gate, and M9's measured fixes. Closing the milestone without them is a decision, recorded with
what it leaves out (GATE-D6). It isn't a hidden gap.

## 10. Interview questions

**"Your eval says the agent passes 6% of tasks. What do you do?"**
> Read failing traces before believing it. Here every failure was the judge rejecting true
> details the agent had read from its tools. The judge's reference facts were incomplete. I fixed
> the facts, added a test that every tool value reaches the judge, and recalibrated it on real
> answers.

**"Your red team found zero successful attacks. Is the agent safe?"**
> Zero only counts if the model answered: an outage also gives zero, and that happened to us. With
> a real run, 0 of 48 means the true rate is below about 7% with 95% confidence. I report
> attempted and executed separately: executed must be 0 because the policy layer stops it;
> attempted measures how steerable the model is.

**"Live evals are expensive. How do you keep costs down?"**
> Run live only when what the model sees changes. Record every response. Rebuild replayable
> recordings from the traces, so one paid run becomes a free regression test. Then account for
> every call, and check the total against the bill.

**"How do you know your baseline is right?"**
> It isn't a number someone typed. It's what the code produces when it replays the recorded
> answers, and CI checks that on every PR. A baseline changes only through a reviewed commit, with
> the report that justifies it.
