import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from evalharness.api import create_app
from evalharness.metrics import wilson_interval
from evalharness.store import ResultsStore

from .fakes import make_run


def scrape(client) -> dict:
    """{(metric_name, frozenset(labels)): value} from /metrics."""
    r = client.get("/metrics/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    out = {}
    for fam in text_string_to_metric_families(r.text):
        for s in fam.samples:
            out[(s.name, frozenset(s.labels.items()))] = s.value
    return out


def labels(**kw):
    return frozenset({"model_id": "mock/mixed@0.1.0", "is_mock": "true", **kw}.items())


@pytest.fixture
def store(tmp_path):
    return ResultsStore(tmp_path / "r.sqlite")


def test_metrics_match_the_report(store):
    store.save_run(make_run("r1", {"a": ["passed", "passed", "timeout"], "b": ["harness_error"]}))
    m = scrape(TestClient(create_app(store.path)))
    overall = labels(task="_all")
    assert m[("evalharness_pass_rate", overall)] == pytest.approx(2 / 3)
    lo, hi = wilson_interval(2, 3)
    assert m[("evalharness_pass_rate_ci_low", overall)] == pytest.approx(lo)
    assert m[("evalharness_pass_rate_ci_high", overall)] == pytest.approx(hi)
    assert m[("evalharness_samples_scored", overall)] == 3
    assert m[("evalharness_samples_excluded", overall)] == 1
    assert m[("evalharness_samples", labels(task="a", category="timeout"))] == 1
    assert m[("evalharness_runs_stored", frozenset())] == 1


def test_no_scored_samples_means_no_rate_not_zero(store):
    store.save_run(make_run("r1", {"b": ["harness_error"]}))
    m = scrape(TestClient(create_app(store.path)))
    assert ("evalharness_pass_rate", labels(task="b")) not in m
    assert m[("evalharness_samples_excluded", labels(task="b"))] == 1


def test_only_the_latest_run_per_model_is_exported(store):
    store.save_run(make_run("old", {"a": ["passed"]}, started_at="2026-11-01T00:00:00+00:00"))
    store.save_run(make_run("new", {"a": ["wrong_answer"]}, started_at="2026-11-05T00:00:00+00:00"))
    m = scrape(TestClient(create_app(store.path)))
    assert m[("evalharness_pass_rate", labels(task="a"))] == 0.0  # from "new", not "old"
    runs = [k for k in m if k[0] == "evalharness_latest_run_start_timestamp_seconds"]
    assert len(runs) == 1 and ("run_id", "new") in runs[0][1]
    assert m[("evalharness_runs_stored", frozenset())] == 2


def test_empty_database_still_scrapes(store):
    m = scrape(TestClient(create_app(store.path)))
    assert m[("evalharness_runs_stored", frozenset())] == 0
    assert not [k for k in m if k[0] == "evalharness_pass_rate"]
