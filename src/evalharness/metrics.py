"""Pass rates with sample sizes and confidence intervals.

Rules (see D14-D15):
- `harness_error` samples are excluded from the pass-rate denominator (they are our failures,
  not the model's) and are always reported as `n_excluded` next to the rate.
- Every rate comes with the sample size it was computed from and a Wilson score interval.
- Nothing here compares models. A summary describes one set of samples.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .scoring import Category

CONFIDENCE = 0.95
Z_95 = 1.959963984540054  # two-sided 95% quantile of the standard normal
# Above this share of excluded samples, a summary is flagged as unreliable.
EXCLUDED_WARN_FRACTION = 0.05


def wilson_interval(passed: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion. None when n == 0.

    Chosen over the textbook "p +/- z*sqrt(p(1-p)/n)" (Wald) interval, which collapses to
    [1, 1] for 5/5 and [0, 0] for 0/5, claiming certainty from five samples. Wilson stays
    inside [0, 1] and behaves sensibly for small n and rates near 0 or 1.
    """
    if n < 0 or passed < 0 or passed > n:
        raise ValueError(f"invalid counts: passed={passed}, n={n}")
    if n == 0:
        return None
    p = passed / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


@dataclass(frozen=True)
class Summary:
    n_samples: int  # every sample, including excluded ones
    n_scored: int  # the pass-rate denominator
    n_excluded: int  # harness_error samples, left out of the rate
    passed: int
    pass_rate: float | None
    ci_low: float | None
    ci_high: float | None
    counts: dict[str, int]  # every category, zeros included, so absences are explicit
    confidence: float = CONFIDENCE
    ci_method: str = "wilson"
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(categories: Iterable[str]) -> Summary:
    c = Counter(str(x) for x in categories)
    unknown = set(c) - {cat.value for cat in Category}
    if unknown:
        raise ValueError(f"unknown categories: {sorted(unknown)}")
    counts = {cat.value: c.get(cat.value, 0) for cat in Category}
    n_samples = sum(counts.values())
    n_excluded = counts[Category.HARNESS_ERROR.value]
    n_scored = n_samples - n_excluded
    passed = counts[Category.PASSED.value]
    ci = wilson_interval(passed, n_scored)
    warning = None
    if n_samples and n_excluded / n_samples > EXCLUDED_WARN_FRACTION:
        warning = (
            f"{n_excluded} of {n_samples} samples were harness errors and are excluded; "
            "fix the infrastructure before trusting this rate"
        )
    elif n_scored == 0:
        warning = "no scored samples"
    return Summary(
        n_samples=n_samples,
        n_scored=n_scored,
        n_excluded=n_excluded,
        passed=passed,
        pass_rate=passed / n_scored if n_scored else None,
        ci_low=ci[0] if ci else None,
        ci_high=ci[1] if ci else None,
        counts=counts,
        warning=warning,
    )


def summarize_samples(samples: Iterable[dict]) -> dict:
    """Overall and per-task summaries from sample records ({"task": {"id"}, "result": {...}})."""
    rows = [(s["task"]["id"], s["result"]["category"]) for s in samples]
    by_task: dict[str, list[str]] = {}
    for task_id, cat in rows:
        by_task.setdefault(task_id, []).append(cat)
    return {
        "overall": summarize(cat for _, cat in rows).to_dict(),
        "by_task": {t: summarize(cats).to_dict() for t, cats in sorted(by_task.items())},
        "note": (
            "The overall interval treats every sample as independent. Samples of the same task "
            "are correlated, so with several samples per task the true uncertainty is wider."
        ),
    }
