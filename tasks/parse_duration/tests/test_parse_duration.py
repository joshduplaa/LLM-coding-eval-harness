import pytest
from solution import parse_duration


def test_single_units():
    assert parse_duration("45s") == 45
    assert parse_duration("2h") == 7200
    assert parse_duration("3m") == 180


def test_combined():
    assert parse_duration("1h30m15s") == 5415
    assert parse_duration("1h15s") == 3615


def test_multi_digit_and_zero():
    assert parse_duration("120m") == 7200
    assert parse_duration("0s") == 0


@pytest.mark.parametrize("bad", ["", "10", "5m1h", "1h1h", "1d", "h", "1h 30m", "-5s", "1.5h"])
def test_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)
