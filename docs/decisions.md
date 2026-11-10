# Decisions

Behaviour and design choices the plan left open, and why. Each names the test that pins it.

---

## ENV-D1: The world is tokenized-settlement-qa v1.0.0, pinned as a git submodule

**Status:** Accepted (M1)

**Decision:** `vendor/settlement/` is a submodule of `tokenized-settlement-qa`, checked out
at the v1.0.0 release commit. Its contracts deploy from its own forge artifacts, and its
services (`indexer`, `transfer_api`) and test helpers (`lib.*`) are imported from it, never
copied. Files under `vendor/` are not edited here.

**Why:**
- The agent needs a realistic system with real failure modes, and that one already exists:
  102 traced requirements, 30 findings, and a release with its evidence pack.
- Pinning a release means the world can't change under the agent's evals. An eval score
  that moves must be the agent's doing, not the environment's.
- Moving to a newer version is a deliberate change with a test update.

**Tests:** ENV-01 (pinned to the exact commit), ENV-02 (services import), ENV-03 (deploys
and moves tokens).

---

## ENV-D2: CI never calls a model

**Status:** Accepted (M1)

**Decision:** the `ci` workflow has no API key. Code that talks to a model is tested with:
- recorded responses (`replay`);
- a deterministic fake model (`scripted`).

Live evaluation runs only in an on-demand workflow with a repository secret (M8).

**Why:**
- PR checks must be free, fast and deterministic.
- A key in every PR run is a leak waiting to happen.
- A test that fails because the model was having a different day teaches people to ignore
  CI. Model behaviour is measured where it can be measured properly: in evals, over
  repeated runs, with confidence intervals.

---

## AQ-D1: The attacker's address is allowlisted on the token

**Status:** Accepted (M2)

**Decision:** the synthetic attacker address (`scenarios.ATTACKER`) is on the settlement
token's allowlist, as if it had been onboarded elsewhere.

**Why:** the token refuses transfers to unlisted addresses. If the attacker weren't listed, a
payee-swap attack would be stopped by the token, and no test could show whether *this* repo's
policy layer works. With the attacker listed, every refusal of a payee swap is the policy
layer's.

**Tests:** TOOL-03 (`test_attacker_address_is_allowlisted_so_only_the_policy_stops_it`, and
the payee-swap case).

---

## AQ-D2: Approval is not a tool; the policy runs at proposal and again at approval

**Status:** Accepted (M2)

**Decision:** the model can only *propose* payments. Approving is `World.approve`, called by a
person (or a test), and no tool exposes it. Every policy rule runs when a payment is proposed
and again when it's approved. Payments over 50,000 USDC are refused outright, not routed to a
second approver: that path is for a human process, not the agent.

**Why:**
- The safest capability is the one the agent doesn't have.
- Re-checking at approval closes the gap between check and use: a vendor blocked or an
  invoice disputed after the proposal must not be paid.

**Tests:** TOOL-02, TOOL-04, TOOL-05, TOOL-06. Mutation checks in learning guide 02: removing
the re-check fails TOOL-06.

---

## AQ-D3: Approved payments go through the settlement transfer API, keyed by invoice

**Status:** Accepted (M2)

**Decision:** an approved payment is submitted to `tokenized-settlement-qa`'s transfer API with
idempotency key `invoice-<id>`, and confirmed by its worker.

**Why:**
- It reuses a payment path whose exactly-once behaviour is already proven (TAPI-05..11 in that
  repository).
- The invoice-based key adds a second, independent duplicate guard below the policy layer.

**Tests:** TOOL-05.
