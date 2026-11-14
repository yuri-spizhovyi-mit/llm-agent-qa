# Changelog

## v1.0.0

The first release: an accounts-payable agent on the Claude API and the suite that evaluates it.
Each milestone is one PR; the details are in [`docs/plan.md`](docs/plan.md).

- **M1 scaffold (#1), M2 world (#2):** the settlement world pinned as a submodule; tools, the
  approval queue and the policy layer, tested without a model.
- **M3 agent (#3):** the Claude tool-use loop; live, record, replay and scripted providers.
- **M4 golden (#4):** 31 tasks scored on chain state, with Wilson intervals and pass^k.
- **M5 judge (#5):** an LLM judge, used only after calibration against labelled answers.
- **M6 red team (#6):** 16 attacks scored attempted vs executed, plus over-blocking; F-06 fixed.
- **M7 gate (#7):** sign and Fisher tests, cost and latency budgets, traces.
- **M8 live (#8–#11):** five live runs ($14.77); the judge's facts (F-10), outages (F-13) and
  cost accounting (F-12) fixed; the baseline is 93 real Claude answers, replayed in CI.
- **M9 harden (#12):** candidate prompt `system_v2` for F-11 (unmeasured); red-team transcripts
  (F-14).
- **M10 release (#13):** the README story, the generated traceability matrix (43 requirements),
  and the evidence pack.

Waiting for API credits: `judge_v2`'s live calibration, a worse prompt blocked on the real model,
and the measurement of `system_v2` (decisions GATE-D6, AGT-D4).
