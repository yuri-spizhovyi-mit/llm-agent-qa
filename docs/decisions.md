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


## AGT-D1: A hand-written agent loop, not an SDK tool runner

**Status:** Accepted (M3)

**Decision:** `agent.py` runs the tool-use loop itself, about 50 lines, instead of using the
SDK's tool runner.

**Why:**
- Evals need to swap the model for recorded and scripted providers, cap the steps, and keep
  every message for scoring. A loop we own makes each of those one line.
- Every exit has a named `Outcome` (completed, refused, truncated, step limit, error) that
  evals can count.
- The loop follows the API contract: assistant content is appended unchanged, and all of a
  turn's tool results go back in one user message (AGT-01).

**Tests:** AGT-01, AGT-02.

## AGT-D2: The system prompt is a versioned file

**Status:** Accepted (M3)

**Decision:** prompts live in `src/agentqa/prompts/system_vN.md`. A run records the version it
used, and changing a prompt means adding a new version rather than editing in place.

**Why:** a prompt change is a behaviour change, like a code change. Eval reports and the M7
gate compare versions, so each must be fixed and nameable. Cassettes are keyed on the exact
prompt text, so an edited prompt can never replay stale answers (AGT-04).

**Tests:** AGT-04, AGT-06.

## AGT-D3: Server-side fallbacks are on, and every fallback-served turn is recorded

**Status:** Accepted (M3). The owner can turn this off.

**Decision:** live requests send the `server-side-fallback-2026-07-01` beta with
`fallbacks="default"`. If the requested model declines a turn, the API can let a fallback model
answer it rather than ending the run. Each `Step` records `served_by` (the model that
answered) and `fallback`.

**Why:**
- For a production agent, a run finished by a fallback model beats a dead run.
- For evals, a fallback-served run measures a different model, so reports must flag or
  exclude it. That's why it is recorded per step.

**Alternative:** no fallbacks. Every refusal ends the run as `REFUSED`, which is simpler to
reason about. It's a one-line change in `providers.py` if preferred.

**Tests:** AGT-05.
