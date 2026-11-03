"""Statistics: paired bootstrap intervals, exact McNemar test, AUROC. Standard library only."""

import random
from collections.abc import Callable, Sequence
from math import comb

BOOTSTRAP_SAMPLES = 1000
SEED = 0


def bootstrap_ci(
    rows: Sequence, metric: Callable[[Sequence], float | None], samples: int = BOOTSTRAP_SAMPLES
) -> list[float] | None:
    """95% percentile interval of `metric` over PR-level resamples (seeded, so reproducible)."""
    if not rows:
        return None
    rng = random.Random(SEED)
    n = len(rows)
    values = []
    for _ in range(samples):
        v = metric([rows[rng.randrange(n)] for _ in range(n)])
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    lo = values[int(0.025 * (len(values) - 1))]
    hi = values[int(0.975 * (len(values) - 1))]
    return [round(lo, 4), round(hi, 4)]


def mcnemar_exact(a_correct: Sequence[bool], b_correct: Sequence[bool]) -> dict:
    """Two-sided exact McNemar test on paired outcomes (same PRs, same order)."""
    if len(a_correct) != len(b_correct):
        raise ValueError("paired outcomes must have the same length")
    b = sum(x and not y for x, y in zip(a_correct, b_correct, strict=True))
    c = sum(y and not x for x, y in zip(a_correct, b_correct, strict=True))
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n)
    return {"n": len(a_correct), "a_only": b, "b_only": c, "p_value": round(p, 6)}


def auroc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """Probability a positive scores above a negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y]
    neg = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 4)
