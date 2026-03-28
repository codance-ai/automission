"""Pre-written tests for calculator. Agent must implement src/calc.py to make these pass."""

import pytest
import sys
import os

# Add src/ to path so we can import calc
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calc import add, subtract, multiply, divide


# ── Basic operations ──


def test_add():
    assert add(2, 3) == 5
    assert add(0, 0) == 0
    assert add(100, 200) == 300


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 0) == 0
    assert subtract(10, 20) == -10


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(0, 100) == 0
    assert multiply(7, 1) == 7


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5
    assert divide(0, 5) == 0.0


# ── Edge cases ──


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(10, 0)
    with pytest.raises(ValueError):
        divide(0, 0)


def test_negative_numbers():
    assert add(-2, -3) == -5
    assert subtract(-5, -3) == -2
    assert multiply(-3, 4) == -12
    assert divide(-10, 2) == -5.0
    assert divide(10, -2) == -5.0
