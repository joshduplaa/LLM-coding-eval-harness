"""Prompt construction and code extraction.

The template is versioned. Changing the wording changes what is being measured, so bump
PROMPT_TEMPLATE_VERSION whenever you edit it. Every run record stores the version and the full
prompt text.
"""

from __future__ import annotations

import re

from .tasks import Task

PROMPT_TEMPLATE_VERSION = "1"

_TEMPLATE = """\
Write a Python 3.12 function that solves the problem below.

Problem: {title}

{description}

Use exactly this signature:

    {signature}

Use only the Python standard library. Do not read input or print output.
Reply with a single ```python code block containing the complete solution.
"""

_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def build_prompt(task: Task) -> str:
    return _TEMPLATE.format(
        title=task.title, description=task.description, signature=task.signature
    )


def extract_code(completion: str) -> str:
    """Return the first fenced code block, or the whole completion if there are no fences.

    Taking the first block is a deliberate, simple rule. Models sometimes put tests or examples
    in later blocks, and joining every block would mix those into the solution.
    """
    m = _FENCE.search(completion)
    return (m.group(1) if m else completion).strip() + "\n"
