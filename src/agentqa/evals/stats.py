"""Statistics for repeated, non-deterministic trials (learning guide 04).

- `wilson`: a 95% confidence interval for a pass rate. Better than "p ± 1.96·√(p(1-p)/n)",
  which gives impossible intervals (below 0, above 1) and zero width at 0/n and n/n.
- `pass_at_k`: the chance that at least one of k attempts passes (capability: "can it?").
- `pass_hat_k` (pass^k): the chance that all k attempts pass (reliability: "does it, every
  time?"). An agent that moves money is judged on pass^k.

Both k-estimators are the unbiased forms over n ≥ k trials with c passes: they average over
every k-subset of the n trials instead of using only the first k.
"""

from __future__ import annotations

from math import comb, sqrt


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = passed / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_at_k(n: int, c: int, k: int) -> float:
    if not 0 < k <= n or not 0 <= c <= n:
        raise ValueError(f"need 0 < k <= n and 0 <= c <= n (n={n}, c={c}, k={k})")
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    if not 0 < k <= n or not 0 <= c <= n:
        raise ValueError(f"need 0 < k <= n and 0 <= c <= n (n={n}, c={c}, k={k})")
    return comb(c, k) / comb(n, k)


def cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    """Agreement between two raters beyond what chance alone would give.

    `pairs` are (rater_a, rater_b) labels. 1.0 is perfect agreement; 0.0 is no better than
    two raters labelling at random with the same label frequencies; negative is worse than
    chance. Accuracy alone hides that: on a set that is 90% "pass", a judge that always says
    "pass" scores 90% accuracy and kappa 0.
    """
    n = len(pairs)
    if n == 0:
        raise ValueError("no pairs")
    labels = {label for pair in pairs for label in pair}
    observed = sum(a == b for a, b in pairs) / n
    expected = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in labels)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)
