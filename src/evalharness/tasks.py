"""Task format and loader.

A task is a directory:

    tasks/<id>/
        task.toml                 metadata + the problem statement shown to the model
        tests/test_*.py           pytest tests; they `from solution import <entry_point>`
        reference/solution.py     known-correct solution (must pass every test)
        reference/incorrect_*.py  intentionally wrong solutions (each must fail)

`content_hash` is a SHA-256 over every file in the directory, so a run record pins exactly which
task content was scored, even if someone edits a task without bumping `version`.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import HarnessError

REQUIRED_KEYS = ("id", "version", "title", "entry_point", "signature", "description")
DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks"


@dataclass(frozen=True)
class Task:
    id: str
    version: str
    title: str
    entry_point: str
    signature: str
    description: str
    timeout_s: float
    path: Path
    test_files: dict[str, str] = field(repr=False)
    reference_solution: str = field(repr=False)
    incorrect_solutions: dict[str, str] = field(repr=False)
    content_hash: str = ""


def _hash_dir(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(path).as_posix().encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def load_task(path: Path) -> Task:
    meta_path = path / "task.toml"
    if not meta_path.is_file():
        raise HarnessError(f"{path}: missing task.toml")
    meta = tomllib.loads(meta_path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        raise HarnessError(f"{meta_path}: missing keys {missing}")
    if meta["id"] != path.name:
        raise HarnessError(f"{meta_path}: id {meta['id']!r} must match directory {path.name!r}")

    tests = {
        p.name: p.read_text(encoding="utf-8") for p in sorted((path / "tests").glob("test_*.py"))
    }
    if not tests:
        raise HarnessError(f"{path}: no tests/test_*.py files")
    ref = path / "reference" / "solution.py"
    if not ref.is_file():
        raise HarnessError(f"{path}: missing reference/solution.py")
    incorrect = {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted((path / "reference").glob("incorrect_*.py"))
    }
    if not incorrect:
        raise HarnessError(f"{path}: needs at least one reference/incorrect_*.py")

    return Task(
        id=meta["id"],
        version=str(meta["version"]),
        title=meta["title"],
        entry_point=meta["entry_point"],
        signature=meta["signature"],
        description=meta["description"].strip(),
        timeout_s=float(meta.get("timeout_s", 10)),
        path=path,
        test_files=tests,
        reference_solution=ref.read_text(encoding="utf-8"),
        incorrect_solutions=incorrect,
        content_hash=_hash_dir(path),
    )


def load_tasks(tasks_dir: Path = DEFAULT_TASKS_DIR, ids: list[str] | None = None) -> list[Task]:
    if not tasks_dir.is_dir():
        raise HarnessError(f"tasks directory not found: {tasks_dir}")
    tasks = [load_task(p) for p in sorted(tasks_dir.iterdir()) if (p / "task.toml").is_file()]
    if ids:
        by_id = {t.id: t for t in tasks}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise HarnessError(f"unknown task id(s): {unknown}. Known: {sorted(by_id)}")
        tasks = [by_id[i] for i in ids]
    return tasks
