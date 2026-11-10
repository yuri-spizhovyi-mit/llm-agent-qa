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
