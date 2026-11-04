from pathlib import Path

import pytest

from evalharness.errors import HarnessError
from evalharness.tasks import load_task, load_tasks

EXPECTED = {"balanced_brackets", "merge_intervals", "parse_duration", "rle_encode", "top_k_words"}


def test_sample_tasks_load():
    tasks = load_tasks()
    assert {t.id for t in tasks} == EXPECTED
    for t in tasks:
        assert t.test_files and t.reference_solution and t.incorrect_solutions
        assert t.entry_point in t.signature
        assert len(t.content_hash) == 64


def test_filter_by_id_and_unknown_id():
    assert [t.id for t in load_tasks(ids=["rle_encode"])] == ["rle_encode"]
    with pytest.raises(HarnessError, match="unknown task"):
        load_tasks(ids=["nope"])


def _make_task(root: Path, **overrides) -> Path:
    d = root / "demo"
    (d / "tests").mkdir(parents=True)
    (d / "reference").mkdir()
    meta = {
        "id": "demo",
        "version": "1",
        "title": "Demo",
        "entry_point": "f",
        "signature": "def f():",
        "description": "Return 1.",
    }
    meta.update(overrides)
    (d / "task.toml").write_text("\n".join(f'{k} = "{v}"' for k, v in meta.items()))
    (d / "tests" / "test_demo.py").write_text(
        "from solution import f\ndef test(): assert f() == 1\n"
    )
    (d / "reference" / "solution.py").write_text("def f():\n    return 1\n")
    (d / "reference" / "incorrect_zero.py").write_text("def f():\n    return 0\n")
    return d


def test_content_hash_changes_when_tests_change(tmp_path):
    d = _make_task(tmp_path)
    h1 = load_task(d).content_hash
    (d / "tests" / "test_demo.py").write_text("from solution import f\ndef test(): assert f()\n")
    assert load_task(d).content_hash != h1


def test_id_must_match_directory(tmp_path):
    d = _make_task(tmp_path, id="other")
    with pytest.raises(HarnessError, match="must match directory"):
        load_task(d)


def test_incorrect_solution_required(tmp_path):
    d = _make_task(tmp_path)
    (d / "reference" / "incorrect_zero.py").unlink()
    with pytest.raises(HarnessError, match="incorrect"):
        load_task(d)
