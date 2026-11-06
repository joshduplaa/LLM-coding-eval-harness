"""Prometheus metrics for the results store, exposed at GET /metrics.

Prometheus scrapes this endpoint periodically and stores each value as a time series that Grafana
can graph. The values are computed from the database at scrape time (a custom "collector"), so
they always match `evalharness report`.

Cardinality is kept bounded on purpose: only the *latest* run of each model is exported, not
every run ever stored. Every series carries `is_mock`, so a dashboard can always label mock data.
Rates come with their sample size and Wilson interval, never alone (D15).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily
from prometheus_client.registry import Collector, CollectorRegistry

from . import __version__
from .metrics import summarize_samples
from .store import ResultsStore

ALL_TASKS = "_all"  # the `task` label value for the overall summary


class ResultsCollector(Collector):
    def __init__(self, store: ResultsStore):
        self.store = store

    def collect(self) -> Iterator:
        yield InfoMetricFamily(
            "evalharness_build", "Harness version", value={"version": __version__}
        )

        runs = self.store.latest_runs_by_model()
        stored = GaugeMetricFamily("evalharness_runs_stored", "Runs in the results database")
        stored.add_metric([], self.store.count_runs())
        yield stored

        labels = ["model_id", "is_mock", "task"]
        rate = GaugeMetricFamily(
            "evalharness_pass_rate", "Pass rate of the latest run (passed / scored)", labels=labels
        )
        low = GaugeMetricFamily(
            "evalharness_pass_rate_ci_low", "Wilson 95% interval, lower bound", labels=labels
        )
        high = GaugeMetricFamily(
            "evalharness_pass_rate_ci_high", "Wilson 95% interval, upper bound", labels=labels
        )
        scored = GaugeMetricFamily(
            "evalharness_samples_scored", "Pass-rate denominator (sample size)", labels=labels
        )
        excluded = GaugeMetricFamily(
            "evalharness_samples_excluded",
            "harness_error samples left out of the rate",
            labels=labels,
        )
        by_cat = GaugeMetricFamily(
            "evalharness_samples", "Samples by result category", labels=[*labels, "category"]
        )
        started = GaugeMetricFamily(
            "evalharness_latest_run_start_timestamp_seconds",
            "When the exported run started (Unix time)",
            labels=["model_id", "is_mock", "run_id"],
        )

        for run in runs:
            model, mock = run["model_id"], "true" if run["is_mock"] else "false"
            started.add_metric([model, mock, run["run_id"]], _unix(run["started_at"]))
            m = summarize_samples(self.store.sample_results(run["run_id"]))
            for task, s in [(ALL_TASKS, m["overall"]), *m["by_task"].items()]:
                key = [model, mock, task]
                scored.add_metric(key, s["n_scored"])
                excluded.add_metric(key, s["n_excluded"])
                if s["pass_rate"] is not None:  # no scored samples: no rate, rather than a fake 0
                    rate.add_metric(key, s["pass_rate"])
                    low.add_metric(key, s["ci_low"])
                    high.add_metric(key, s["ci_high"])
                for cat, n in s["counts"].items():
                    by_cat.add_metric([*key, cat], n)

        yield from (rate, low, high, scored, excluded, by_cat, started)


def build_registry(store: ResultsStore) -> CollectorRegistry:
    """A registry per app (not the global default), so tests and multiple apps stay isolated."""
    registry = CollectorRegistry(auto_describe=True)
    registry.register(ResultsCollector(store))
    return registry


def _unix(iso: str) -> float:
    return dt.datetime.fromisoformat(iso).timestamp()
