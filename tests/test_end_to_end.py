"""End-to-end through the CLI and the runner (needs Docker)."""

import json

import pytest

from evalharness.cli import main
from evalharness.runner import validate_task
from evalharness.sandbox import SandboxLimits
from evalharness.tasks import load_tasks

REQUIRED_SAMPLE_FIELDS = {
    "task",
    "sample_index",
    "model_id",
    "is_mock",
    "prompt",
    "completion",
    "generated_code",
    "result",
    "sandbox",
    "finished_at",
}


@pytest.mark.docker
@pytest.mark.parametrize("task", load_tasks(), ids=lambda t: t.id)
def test_every_task_separates_reference_from_incorrect(task):
    v = validate_task(task, SandboxLimits())
    assert v.ok, v.checks
    assert v.expected_tests > 0


@pytest.mark.docker
def test_cli_run_writes_reproducible_record(tmp_path, capsys):
    rc = main(
        [
            "run",
            "--model",
            "mock-reference",
            "--task",
            "rle_encode",
            "--task",
            "top_k_words",
            "--samples",
            "2",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("MOCK MODEL") >= 2  # banner at top and bottom

    (path,) = tmp_path.glob("*.json")
    run = json.loads(path.read_text())
    assert run["is_mock"] is True and "NOT a real model result" in run["notice"]
    assert run["harness"]["version"] and run["harness"]["prompt_template_version"] == "1"
    assert run["model"]["params"] == {"temperature": 0.0, "max_tokens": 1024, "seed": 0}
    assert all(v["ok"] for v in run["validations"])
    assert len(run["samples"]) == 4
    for s in run["samples"]:
        assert REQUIRED_SAMPLE_FIELDS <= s.keys()
        assert s["result"]["category"] == "passed"
        assert s["task"]["version"] and len(s["task"]["content_hash"]) == 64
        assert s["sandbox"]["image_id"].startswith("sha256:")
        assert s["sandbox"]["test_returncode"] == 0
        assert s["sandbox"]["test_output"]
        assert s["sandbox"]["duration_s"] > 0


@pytest.mark.docker
def test_cli_mock_incorrect_is_recorded_as_wrong_answer(tmp_path):
    assert (
        main(
            [
                "run",
                "--model",
                "mock-incorrect",
                "--task",
                "merge_intervals",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    run = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert run["samples"][0]["result"]["category"] == "wrong_answer"


def test_cli_rejects_real_model(capsys):
    assert main(["run", "--model", "gpt-whatever"]) == 2
    assert "need approval" in capsys.readouterr().err


@pytest.mark.docker
def test_cli_run_is_stored_and_reported(tmp_path, capsys, _isolated_results_db):
    args = ["run", "--model", "mock-mixed", "--seed", "1", "--task", "rle_encode"]
    assert main([*args, "--samples", "3", "--out", str(tmp_path)]) == 0
    run = json.loads(next(tmp_path.glob("*.json")).read_text())
    out = capsys.readouterr().out
    assert "95% CI (Wilson)" in out and "OVERALL" in out and out.count("MOCK MODEL") >= 2

    # The stored copy matches the JSON file, and the report recomputes the same numbers.
    from evalharness.store import ResultsStore

    store = ResultsStore(_isolated_results_db)
    assert store.get_sample(run["run_id"], "rle_encode", 2) == run["samples"][2]
    assert main(["report", run["run_id"], "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    cats = [s["result"]["category"] for s in run["samples"]]
    assert report["is_mock"] is True
    assert report["overall"]["n_samples"] == 3
    assert report["overall"]["passed"] == cats.count("passed")


def test_cli_import_and_runs(tmp_path, capsys):
    from .fakes import make_run

    f = tmp_path / "r.json"
    f.write_text(json.dumps(make_run("imported-1", {"a": ["passed"]})))
    assert main(["import", str(f)]) == 0
    assert main(["runs"]) == 0
    assert "imported-1  MOCK" in capsys.readouterr().out
    assert main(["import", str(f)]) == 2  # same run twice is refused
