"""
Tests the scoring/promotion logic in aggregator_service without hitting any
real network — this is the part that decides whether a scraped/API product
goes live automatically, so it's worth locking down with tests.
"""
import pytest
from app.services.aggregator_service import _score_candidate, AUTO_PROMOTE_THRESHOLD


def test_zero_price_never_scores():
    assert _score_candidate(demand_score=100, rating=5, review_count=10000, price=0) == 0.0


def test_negative_price_never_scores():
    assert _score_candidate(demand_score=100, rating=5, review_count=10000, price=-5) == 0.0


def test_high_signal_product_can_reach_auto_promote_threshold():
    score = _score_candidate(demand_score=100, rating=5, review_count=1000, price=29.99)
    assert score >= AUTO_PROMOTE_THRESHOLD


def test_low_signal_product_stays_below_threshold():
    score = _score_candidate(demand_score=10, rating=2, review_count=1, price=29.99)
    assert score < AUTO_PROMOTE_THRESHOLD


def test_score_is_bounded_0_to_100():
    score = _score_candidate(demand_score=99999, rating=999, review_count=999999, price=1)
    assert 0 <= score <= 100
