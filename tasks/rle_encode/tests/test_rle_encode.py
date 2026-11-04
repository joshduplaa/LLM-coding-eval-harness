from solution import rle_encode


def test_examples():
    assert rle_encode("aaabcc") == "a3bc2"
    assert rle_encode("abc") == "abc"


def test_empty():
    assert rle_encode("") == ""


def test_single_long_run():
    assert rle_encode("z" * 12) == "z12"


def test_run_at_end():
    assert rle_encode("abbb") == "ab3"


def test_non_adjacent_repeats_are_separate_runs():
    assert rle_encode("aabaa") == "a2ba2"
