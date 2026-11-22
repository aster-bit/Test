"""Small statistics helpers (no scipy dependency)."""

from __future__ import annotations

import math
from statistics import NormalDist


def wilson(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    # exact endpoints at the boundaries (avoid 0.9999999999 from floating-point cancellation)
    if successes == n:
        hi = 1.0
    if successes == 0:
        lo = 0.0
    return (lo, hi)


def zero_failure_upper_bound(n: int, confidence: float = 0.95) -> float:
    """Exact one-sided upper bound on a failure rate after 0 failures in n trials."""
    if n == 0:
        return 1.0
    return 1 - (1 - confidence) ** (1 / n)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return v[int(k)]
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
