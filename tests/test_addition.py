import pytest

from tools.addition import add


def test_add_positive_numbers():
    assert add(2, 3) == 5


def test_add_with_zero():
    assert add(0, 5) == 5
    assert add(5, 0) == 5
    assert add(0, 0) == 0


def test_add_negative_numbers():
    assert add(-2, -3) == -5


def test_add_mixed_sign_numbers():
    assert add(-5, 3) == -2
    assert add(5, -3) == 2


def test_add_float_values():
    assert add(2.5, 3.1) == pytest.approx(5.6)
    assert add(-1.5, 1.5) == pytest.approx(0)


def test_add_very_large_numbers():
    assert add(10**18, 10**18) == 2 * 10**18
    assert add(-(10**18), 10**18) == 0
