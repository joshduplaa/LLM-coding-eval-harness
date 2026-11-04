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
