from solution import merge_intervals


def test_basic_overlap():
    assert merge_intervals([(1, 4), (2, 6), (8, 9)]) == [(1, 6), (8, 9)]


def test_touching_endpoints_merge():
    assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]


def test_unsorted_input():
    assert merge_intervals([(8, 9), (1, 2), (2, 4)]) == [(1, 4), (8, 9)]


def test_containment():
    assert merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]


def test_empty_and_single():
    assert merge_intervals([]) == []
    assert merge_intervals([(5, 5)]) == [(5, 5)]


def test_input_not_mutated():
    data = [(3, 4), (1, 2)]
    merge_intervals(data)
    assert data == [(3, 4), (1, 2)]
