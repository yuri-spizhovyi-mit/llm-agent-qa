# Findings

What the tests and evals found: planted bugs they were proven against, real defects in the
agent or its guardrails, successful attacks, and bugs in the test harness itself. Each
entry gives the evidence and the outcome.

| ID | Found by | Kind | Status |
|---|---|---|---|
| F-01 | mutation check of AGT-03 | test-harness bug: the fuzzer never reached a live state | Fixed in M3 |
| F-02 | mutation check of AGT-03 | duplicate guard: the books and the chain disagree when the policy check is missing | Guarded twice; caught by a bookkeeping invariant |
| F-03 | mutation check of the eval scorers | two scorer checks (`must_call`, `must_not_call`) could be deleted with every test green | Fixed in M4 |

## F-01: the worst-case model's first version tested only two of six rules

**Found by:** mutation checks on `tests/test_agent.py::test_worst_case_model_cannot_move_money_unsafely`
(AGT-03). Each policy rule was disabled in turn.

**Evidence:** removing the payee or amount check turned AGT-03 red. Removing the
blocked-vendor, invoice-not-open, duplicate or over-limit check left it **green on all 5
seeds**. Printing the payments showed none at all: the model picked invoice, address and
amount independently at random. It almost never combined a vendor's real address with that
invoice's exact amount, so no proposal ever got past the payee check. The money invariants
held trivially, because nothing ever moved. Only the "attacks were tried" assertion went red.

**Fix:** the worst-case model is now adversarially *smart*. Most proposals use the invoice's
real payee and exact amount, so the one rule that invoice breaks is the only thing in the
way. The test also asserts that the run reached live states: every rule fired at least once,
and some payments really settled. After the fix, **all 8 mutants** (6 policy rules, 2 loop
bugs) turn the suite red.

**Lesson:** the same as F-02..F-04 in `tokenized-settlement-qa`. A fuzzer or invariant run
that never reaches an interesting state passes for the wrong reason. Always prove the run
reached the states you care about.

## F-02: without the policy's duplicate check, money stays right but the books go wrong

**Found by:** the same mutation run, with the duplicate check disabled.

**Evidence:** the careless approver approved every proposal. **INV-101 ended with 12
`confirmed` payments**, but the vendor received 1,250.50 USDC exactly once. All 12 approvals
reached the transfer API with the same idempotency key `invoice-INV-101`, which returned one
transfer (AQ-D3). The chain was right; the agent's books claimed 12 payments.

**Outcome:** no defect in the shipped code, because the policy check exists. The finding
shows why both guards matter:
- the idempotency key stops double *payment*;
- the policy stops double *bookkeeping*.

AGT-03's "never twice" and treasury-reconciliation assertions catch the second case, so a
regression in either guard is caught.


## F-03: two eval checks could be deleted without any test noticing

**Found by:** mutation checks on `src/agentqa/evals/scoring.py`, one check disabled at a time.

**Evidence:** four mutants were killed: proposals checked as a subset, mentions ignored, no
revert between trials, fallback trials counted. Two survived, with all 21 tests green:
`must_call` and `must_not_call` always true. The flawed agents in GOLD-03 always failed those
tasks on *another* check (proposals or mentions), so nothing depended on these two.

**Fix:** a test for the exact case each check exists for:
- `tests/test_golden.py::test_a_right_answer_without_looking_is_a_failure`: the right number,
  but no tool was read, which means a guess;
- `tests/test_golden.py::test_trying_to_pay_on_a_question_is_a_failure_even_when_refused`: the
  policy refused, so no proposal exists, but the attempt is the bug.

Both mutants now fail.

**Lesson:** "every task can fail" (GOLD-03) is not the same as "every check can fail". A check
that never decides an outcome is dead code in the scorer, and it will silently stay dead when
someone breaks it.
