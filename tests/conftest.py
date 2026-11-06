import os

import pytest

from evalharness.errors import HarnessError
from evalharness.sandbox import DEFAULT_IMAGE, DEFAULT_RUNTIME, available_runtimes, image_id


def _sandbox_ready() -> str | None:
    try:
        image_id(DEFAULT_IMAGE)
        if DEFAULT_RUNTIME not in available_runtimes():
            return f"Docker has no {DEFAULT_RUNTIME!r} runtime registered"
        return None
    except HarnessError as e:
        return str(e)


_REASON = _sandbox_ready()


def pytest_report_header(config):
    return f"evalharness sandbox runtime: {DEFAULT_RUNTIME}"


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
