from evalharness.prompts import PROMPT_TEMPLATE_VERSION, build_prompt, extract_code
from evalharness.tasks import load_tasks


def test_prompt_contains_task_and_signature():
    t = load_tasks(ids=["rle_encode"])[0]
    p = build_prompt(t)
    assert t.signature in p and t.title in p and "```python" in p
    assert PROMPT_TEMPLATE_VERSION == "1"


def test_extract_first_fenced_block():
    text = "Here:\n```python\ndef f():\n    return 1\n```\nTests:\n```python\nassert f()\n```"
    assert extract_code(text) == "def f():\n    return 1\n"


def test_extract_unlabeled_fence_and_no_fence():
    assert extract_code("```\nx = 1\n```") == "x = 1\n"
    assert extract_code("def g(): pass") == "def g(): pass\n"
