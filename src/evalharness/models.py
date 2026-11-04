"""Model interface and the mock model.

Real model adapters will implement the same `Model` protocol. None exist yet: real model calls
cost money and need credentials, so they wait for explicit approval.

The mock is deliberately transparent. Its id starts with "mock/", it sets is_mock=True, and
everything it returns is canned text derived from the task's own reference files. A mock
result is never evidence about any real model.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from .prompts import build_prompt
from .tasks import Task

MOCK_VERSION = "0.1.0"
MOCK_BEHAVIORS = ("reference", "incorrect", "mixed")


@dataclass(frozen=True)
class GenerationParams:
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Completion:
    text: str
    model_id: str
    is_mock: bool
    latency_s: float


class Model(Protocol):
    model_id: str
    is_mock: bool

    def generate(self, prompt: str, params: GenerationParams) -> Completion: ...


_SYNTAX_ERROR = "def {name}(*args, **kwargs)\n    return None\n"  # missing colon
_INFINITE_LOOP = "def {name}(*args, **kwargs):\n    while True:\n        pass\n"


class MockModel:
    """Returns canned completions. It looks up the task by the exact prompt text, so it sees
    only what a real model would see: the prompt.

    behaviors:
      reference  -> the task's known-correct solution (should pass)
      incorrect  -> the task's first intentionally wrong solution (should fail)
      mixed      -> a seeded random pick per sample: reference, incorrect, a syntax error,
                    or an infinite loop. Useful for seeing every result category end to end.
    """

    is_mock = True

    def __init__(self, tasks: list[Task], behavior: str = "reference"):
        if behavior not in MOCK_BEHAVIORS:
            raise ValueError(f"unknown mock behavior {behavior!r}; choose from {MOCK_BEHAVIORS}")
        self.behavior = behavior
        self.model_id = f"mock/{behavior}@{MOCK_VERSION}"
        self._by_prompt = {build_prompt(t): t for t in tasks}
        self._calls: dict[str, int] = {}

    def generate(self, prompt: str, params: GenerationParams) -> Completion:
        start = time.perf_counter()
        task = self._by_prompt.get(prompt)
        if task is None:
            raise ValueError("MockModel received a prompt it was not built for")
        n = self._calls.get(task.id, 0)
        self._calls[task.id] = n + 1

        choice = self.behavior
        if choice == "mixed":
            rng = random.Random(f"{params.seed}:{task.id}:{n}")  # noqa: S311 - reproducibility, not crypto
            choice = rng.choice(["reference", "incorrect", "syntax_error", "infinite_loop"])

        if choice == "reference":
            code = task.reference_solution
        elif choice == "incorrect":
            code = next(iter(task.incorrect_solutions.values()))
        elif choice == "syntax_error":
            code = _SYNTAX_ERROR.format(name=task.entry_point)
        else:
            code = _INFINITE_LOOP.format(name=task.entry_point)

        text = f"[MOCK OUTPUT — {choice}]\n```python\n{code.rstrip()}\n```\n"
        return Completion(
            text=text,
            model_id=self.model_id,
            is_mock=True,
            latency_s=time.perf_counter() - start,
        )


def make_model(spec: str, tasks: list[Task]) -> Model:
    """Turn a CLI model spec into a model. Only mock specs are accepted for now."""
    if spec.startswith("mock-"):
        return MockModel(tasks, behavior=spec.removeprefix("mock-"))
    raise ValueError(
        f"unknown model {spec!r}. Only mock models are available "
        f"({', '.join('mock-' + b for b in MOCK_BEHAVIORS)}); real models need approval."
    )
