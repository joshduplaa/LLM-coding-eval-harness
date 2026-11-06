import pytest
from fastapi.testclient import TestClient

from evalharness.api import MOCK_NOTICE, create_app
from evalharness.store import ResultsStore

from .fakes import make_run


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "results.sqlite"
    ResultsStore(db).save_run(
        make_run("r1", {"a": ["passed", "passed", "harness_error"], "b": ["timeout"]})
    )
    return TestClient(create_app(db))


def test_health_and_tasks(client):
    assert client.get("/healthz").json()["status"] == "ok"
    ids = {t["id"] for t in client.get("/tasks").json()}
    assert {"rle_encode", "parse_duration"} <= ids


def test_every_mock_response_carries_the_notice(client):
    for path in [
        "/runs",
        "/runs/r1",
        "/runs/r1/metrics",
        "/runs/r1/samples",
        "/runs/r1/samples/a/0",
    ]:
        body = client.get(path).json()
        items = body if isinstance(body, list) else [body]
        for item in items:
            assert item["is_mock"] is True and item["notice"] == MOCK_NOTICE, path


def test_metrics_report_n_and_interval(client):
    m = client.get("/runs/r1/metrics").json()
    o = m["overall"]
    assert (o["n_samples"], o["n_scored"], o["n_excluded"], o["passed"]) == (4, 3, 1, 2)
    assert o["ci_method"] == "wilson" and 0 < o["ci_low"] < o["pass_rate"] < o["ci_high"] < 1
    assert m["by_task"]["b"]["counts"]["timeout"] == 1


def test_sample_filters_and_validation(client):
    rows = client.get("/runs/r1/samples", params={"category": "passed"}).json()["samples"]
    assert len(rows) == 2 and all(r["category"] == "passed" for r in rows)
    assert client.get("/runs/r1/samples", params={"category": "nonsense"}).status_code == 422
    assert client.get("/runs/r1/samples", params={"limit": 0}).status_code == 422


def test_not_found(client):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/metrics").status_code == 404
    assert client.get("/runs/r1/samples/a/99").status_code == 404


def test_api_is_read_only(client):
    assert client.post("/runs", json={}).status_code == 405
    assert client.delete("/runs/r1").status_code == 405
