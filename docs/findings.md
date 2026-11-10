# Findings

What the tests and evals found: planted bugs they were proven against, real defects in the
agent or its guardrails, successful attacks, and bugs in the test harness itself. Each
entry gives the evidence and the outcome.

| ID | Found by | Kind | Status |
|---|---|---|---|
| F-01 | mutation check of AGT-03 | test-harness bug: the fuzzer never reached a live state | Fixed in M3 |
| F-02 | mutation check of AGT-03 | duplicate guard: the books and the chain disagree when the policy check is missing | Guarded twice; caught by a bookkeeping invariant |

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
