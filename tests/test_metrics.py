import math

import pytest

from evalharness.metrics import summarize, summarize_samples, wilson_interval
from evalharness.scoring import Category

from .fakes import make_run


def wilson_by_quadratic(k, n, z=1.959963984540054):
    """Independent derivation: solve |p_hat - p| = z * sqrt(p(1-p)/n) for p."""
    ph = k / n
    a = 1 + z * z / n
    b = -(2 * ph + z * z / n)
    c = ph * ph
    disc = math.sqrt(b * b - 4 * a * c)
    return (-b - disc) / (2 * a), (-b + disc) / (2 * a)


@pytest.mark.parametrize(
    "k,n,low,high",
    [
        (0, 10, 0.0, 0.2775),  # commonly published reference values
        (10, 10, 0.7225, 1.0),
        (4, 10, 0.1682, 0.6873),
        (5, 5, 0.5655, 1.0),
    ],
)
def test_wilson_matches_reference_values(k, n, low, high):
    lo, hi = wilson_interval(k, n)
    assert lo == pytest.approx(low, abs=1e-4) and hi == pytest.approx(high, abs=1e-4)


@pytest.mark.parametrize("k,n", [(1, 3), (7, 20), (50, 100), (999, 1000)])
def test_wilson_matches_independent_derivation(k, n):
    assert wilson_interval(k, n) == pytest.approx(wilson_by_quadratic(k, n), abs=1e-12)


def test_wilson_narrows_as_n_grows_and_never_claims_certainty():
    small, large = wilson_interval(5, 10), wilson_interval(500, 1000)
    assert (small[1] - small[0]) > (large[1] - large[0])
    assert wilson_interval(5, 5)[0] < 1.0  # 5/5 is not proof of a 100% pass rate


def test_wilson_edge_cases():
    assert wilson_interval(0, 0) is None
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_harness_errors_are_excluded_from_the_denominator_and_reported():
    s = summarize(["passed"] * 4 + ["wrong_answer"] * 5 + ["harness_error"])
    assert (s.n_samples, s.n_scored, s.n_excluded, s.passed) == (10, 9, 1, 4)
    assert s.pass_rate == pytest.approx(4 / 9)
    assert (s.ci_low, s.ci_high) == pytest.approx(wilson_interval(4, 9))
    assert s.warning and "excluded" in s.warning  # 10% excluded is above the 5% threshold


def test_counts_list_every_category_including_zeros():
    s = summarize(["passed"])
    assert set(s.counts) == {c.value for c in Category}
    assert s.counts["timeout"] == 0 and s.warning is None


def test_all_excluded_gives_no_rate():
    s = summarize(["harness_error"] * 3)
    assert s.n_scored == 0 and s.pass_rate is None and s.ci_low is None and s.warning


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        summarize(["passed", "kinda_passed"])


def test_summarize_samples_overall_and_by_task():
    run = make_run("r1", {"a": ["passed", "passed"], "b": ["timeout", "passed"]})
    m = summarize_samples(run["samples"])
    assert m["overall"]["passed"] == 3 and m["overall"]["n_scored"] == 4
    assert m["by_task"]["a"]["pass_rate"] == 1.0
    assert m["by_task"]["b"]["counts"]["timeout"] == 1
    assert "independent" in m["note"]
