"""Calculator tests for M2 iterative fixture.

These tests require edge case handling that's unlikely on first attempt.
"""

from src.calc import add, subtract, multiply, divide
import pytest


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(1, 5) == -4
    assert subtract(0, 0) == 0


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 3) == -6
    assert multiply(0, 100) == 0


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5
    assert divide(-6, 3) == -2.0


def test_divide_by_zero():
    with pytest.raises(ValueError, match="zero"):
        divide(5, 0)
    with pytest.raises(ValueError, match="zero"):
        divide(0, 0)


def test_negative_numbers():
    assert add(-5, -3) == -8
    assert subtract(-5, -3) == -2
    assert multiply(-4, -3) == 12
    assert divide(-10, -2) == 5.0


def test_float_inputs():
    assert add(1.5, 2.5) == 4.0
    assert subtract(5.5, 2.0) == 3.5
    assert multiply(2.5, 4.0) == 10.0
    assert divide(7.5, 2.5) == 3.0


def test_multiply_by_zero():
    assert multiply(0, 0) == 0
    assert multiply(999, 0) == 0
    assert multiply(0, -5) == 0
