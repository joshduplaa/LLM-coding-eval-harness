import json

import pytest

from evalharness.errors import HarnessError
from evalharness.store import ResultsStore

from .fakes import make_run


@pytest.fixture
def store(tmp_path):
    return ResultsStore(tmp_path / "results.sqlite")


def test_round_trip_keeps_full_records(store):
    run = make_run("r1", {"a": ["passed", "wrong_answer"], "b": ["timeout"]})
    store.save_run(run)
    doc = store.get_run("r1")
    assert "samples" not in doc and doc["validations"] == run["validations"]
    assert doc["notice"].startswith("MOCK")
    assert store.get_sample("r1", "a", 1) == run["samples"][1]  # identical, field for field


def test_list_and_filter(store):
    store.save_run(make_run("old", {"a": ["passed"]}, started_at="2026-11-01T00:00:00+00:00"))
    store.save_run(make_run("new", {"a": ["passed", "timeout"], "b": ["timeout"]}))
    runs = store.list_runs()
    assert [r["run_id"] for r in runs] == ["new", "old"]  # newest first
    assert runs[0]["n_samples"] == 3 and runs[0]["is_mock"] is True
    assert len(store.sample_rows("new", category="timeout")) == 2
    assert len(store.sample_rows("new", task_id="a")) == 2
    assert [
        r["sample_index"] for r in store.sample_rows("new", task_id="a", limit=1, offset=1)
    ] == [1]
    assert "record" not in store.sample_rows("new")[0]  # summary rows stay small


def test_runs_are_append_only(store):
    store.save_run(make_run("r1", {"a": ["passed"]}))
    with pytest.raises(HarnessError):
        store.save_run(make_run("r1", {"a": ["wrong_answer"]}))
    assert store.sample_rows("r1")[0]["category"] == "passed"  # original untouched


def test_failed_save_writes_nothing(store):
    run = make_run("r1", {"a": ["passed", "passed"]})
    run["samples"][1]["sample_index"] = 0  # duplicate key: second insert fails
    with pytest.raises(HarnessError):
        store.save_run(run)
    assert store.get_run("r1") is None and store.sample_rows("r1") == []  # rolled back


def test_missing_things_return_none(store):
    assert store.get_run("nope") is None and store.get_sample("nope", "a", 0) is None


def test_unknown_schema_version_is_refused(tmp_path):
    import sqlite3

    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(HarnessError):
        ResultsStore(path)


def test_hostile_text_is_stored_as_data(store):
    run = make_run("r1", {"a": ["wrong_answer"]})
    run["samples"][0]["generated_code"] = "'); DROP TABLE runs; --"
    store.save_run(run)
    assert json.loads(json.dumps(store.get_sample("r1", "a", 0)))["generated_code"].startswith("'")
    assert store.get_run("r1") is not None
