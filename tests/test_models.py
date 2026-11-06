import pytest

from evalharness.models import GenerationParams, MockModel, make_model
from evalharness.prompts import build_prompt, extract_code
from evalharness.tasks import load_tasks


@pytest.fixture(scope="module")
def tasks():
    return load_tasks()


def test_mock_is_labeled(tasks):
    m = make_model("mock-reference", tasks)
    c = m.generate(build_prompt(tasks[0]), GenerationParams())
    assert m.is_mock and c.is_mock
    assert c.model_id.startswith("mock/")
    assert "MOCK OUTPUT" in c.text


def test_mock_reference_returns_reference(tasks):
    m = MockModel(tasks, "reference")
    for t in tasks:
        code = extract_code(m.generate(build_prompt(t), GenerationParams()).text)
        assert code.strip() == t.reference_solution.strip()


def test_mixed_is_deterministic_for_a_seed(tasks):
    def run(seed):
        m = MockModel(tasks, "mixed")
        p = GenerationParams(seed=seed)
        return [m.generate(build_prompt(t), p).text for t in tasks for _ in range(3)]

    assert run(7) == run(7)
    assert run(7) != run(8)


def test_unknown_prompt_and_real_model_rejected(tasks):
    with pytest.raises(ValueError):
        MockModel(tasks).generate("something else", GenerationParams())
    with pytest.raises(ValueError, match="need approval"):
        make_model("claude-opus", tasks)
