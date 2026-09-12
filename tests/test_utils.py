import pytest
from utils import calculate_score


def test_perfect_score():
    # 100 % success + 20 ms avg → score 100
    assert calculate_score(10, 10, [20.0] * 10) == 100.0


def test_all_failed_no_rts():
    # 0 successes, no response times → score 0
    assert calculate_score(0, 10, []) == 0.0


def test_zero_total():
    # No results at all → score 0 (no division by zero)
    assert calculate_score(0, 0, []) == 0.0


def test_partial_success():
    # 50 % success, fast responses → between 0 and 100
    score = calculate_score(5, 10, [20.0] * 5)
    assert 0 < score < 100


def test_slow_responses_cap():
    # 100 % success but 500 ms avg → RT score is 0, total ≤ 50
    score = calculate_score(10, 10, [500.0] * 10)
    assert score <= 50.0


def test_very_slow_responses_clamped():
    # RT score never goes below 0
    score = calculate_score(10, 10, [9999.0] * 10)
    assert score >= 0.0


def test_score_never_exceeds_100():
    # No edge case should push score above 100
    score = calculate_score(100, 100, [1.0] * 100)
    assert score <= 100.0


def test_score_is_float():
    assert isinstance(calculate_score(5, 10, [50.0]), float)
