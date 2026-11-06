from solution import top_k_words


def test_basic():
    assert top_k_words("the cat and the hat and the bat", 2) == ["the", "and"]


def test_case_insensitive_and_punctuation():
    assert top_k_words("Go, go! GO? stop.", 1) == ["go"]


def test_ties_broken_alphabetically():
    assert top_k_words("pear apple fig apple fig pear", 3) == ["apple", "fig", "pear"]


def test_k_larger_than_vocabulary():
    assert top_k_words("one two", 5) == ["one", "two"]


def test_k_zero_and_empty_text():
    assert top_k_words("anything", 0) == []
    assert top_k_words("", 3) == []


def test_digits_split_words():
    assert top_k_words("abc123abc", 1) == ["abc"]
