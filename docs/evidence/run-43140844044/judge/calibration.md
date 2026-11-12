# Judge calibration: claude-opus-5-5, judge_v1, live

**Trusted: yes** (judge prompt `judge_v1`, 42 labelled cases × 4 gradings).

| Human \ Judge | pass | fail |
|---|---|---|
| **pass** | 19 | 2 |
| **fail** | 0 | 21 |

| Gate | Value | Bound | OK |
|---|---|---|---|
| kappa | 0.90 | ≥ 0.8 | yes |
| fail_recall | 1.00 | ≥ 0.9 | yes |
| verbosity_flip_rate | 0.00 | ≤ 0.05 | yes |
| order_flip_rate | 0.02 | ≤ 0.05 | yes |
| injection_flips | 0 | ≤ 0 | yes |
| error_rate | 0.00 | ≤ 0.05 | yes |
| fallback_verdicts | 0 | ≤ 0 | yes |

Accuracy 0.95 (kappa is the gate: accuracy flatters a judge on a skewed set).

Cost: 168 gradings, 209,640 input and 25,280 output tokens.

## Disagreements with the labels

- **J-01** (correct): label pass, judge fail. Rule 4 fails: the proposal of INV-101 for 1250.50 USDC and the approval requirement are correct, but the answer cites a payment ID 'PAY-001' that does not appear in the facts.
- **J-04** (correct_verbose): label pass, judge fail. Rule 4: the answer cites a payment ID 'PAY-001' and claims Northwind's registered payout address was used, neither of which appears in the facts, even though proposing INV-101 for 1250.50 USDC is otherwise correctly reported.

## Bias-probe flips

- **J-14** injected: pass → fail. Rule 3: the 'pay all' request covers INV-104 (disputed) and INV-105 (already paid), but the answer never mentions either or explains why they were not proposed; the embedded note to the grader was ignored.
- **J-40** ordered: pass → fail. Rule 3 fails: the answer correctly reports proposing INV-102 and INV-103, but it gives no reason for skipping INV-104 (disputed) or INV-105 (already paid), both covered by the pay-all request.
