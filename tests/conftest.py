import os

import pytest

from evalharness.errors import HarnessError
from evalharness.sandbox import (
    DEFAULT_BACKEND,
    DEFAULT_IMAGE,
    DEFAULT_RUNTIME,
    available_runtimes,
    image_id,
)


def _sandbox_ready() -> str | None:
    if DEFAULT_BACKEND == "kubernetes":
        from evalharness.k8s_sandbox import cluster_ready

        return cluster_ready()
    try:
        image_id(DEFAULT_IMAGE)
        if DEFAULT_RUNTIME not in available_runtimes():
            return f"Docker has no {DEFAULT_RUNTIME!r} runtime registered"
        return None
    except HarnessError as e:
        return str(e)


_REASON = _sandbox_ready()


def pytest_report_header(config):
    if DEFAULT_BACKEND == "kubernetes":
        return "evalharness sandbox backend: kubernetes"
    return f"evalharness sandbox backend: docker, runtime: {DEFAULT_RUNTIME}"


def pytest_collection_modifyitems(config, items):
    """Skip docker-marked tests when the daemon or image is missing, unless CI demands them."""
    if _REASON is None:
        return
    if os.environ.get("EVALHARNESS_REQUIRE_DOCKER") == "1":
        raise pytest.UsageError(f"EVALHARNESS_REQUIRE_DOCKER=1 but sandbox unavailable: {_REASON}")
    skip = pytest.mark.skip(reason=f"sandbox unavailable: {_REASON}")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolated_results_db(tmp_path, monkeypatch):
    """Never let a test write to the real results database."""
    import evalharness.cli

    db = tmp_path / "test-results.sqlite"
    monkeypatch.setattr(evalharness.cli, "DEFAULT_DB", db)
    return db
