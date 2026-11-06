from solution import is_balanced


def test_balanced_examples():
    assert is_balanced("a(b[c]{d})") is True
    assert is_balanced("") is True
    assert is_balanced("no brackets at all") is True


def test_wrong_nesting_order():
    assert is_balanced("([)]") is False


def test_unclosed_and_unopened():
    assert is_balanced("(()") is False
    assert is_balanced("())") is False
    assert is_balanced("}{") is False


def test_deep_nesting():
    assert is_balanced("[" * 500 + "]" * 500) is True
