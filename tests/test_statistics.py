"""
Tests for statistical calculations and historical metrics.
"""

import pytest
from app.services.statistics_service import compute_statistics


def test_statistics_empty():
    """Verify statistics handling when no observations exist."""
    stats = compute_statistics([])
    assert stats["observation_count"] == 0
    assert stats["has_sufficient_data"] is False
    assert stats["min_price"] is None
    assert stats["max_price"] is None
    assert stats["avg_price"] is None
    assert stats["current_price"] is None
    assert stats["vs_min_abs"] is None
    assert stats["vs_min_pct"] is None
    assert stats["is_at_historical_low"] is False


def test_statistics_single_observation():
    """Verify single observation returns correct metrics and insufficient data flag."""
    obs = [{"selling_price": 16950.0, "observed_at": "2026-11-14T08:00:00Z"}]
    stats = compute_statistics(obs)

    assert stats["observation_count"] == 1
    assert stats["has_sufficient_data"] is False
    assert stats["min_price"] == 16950.0
    assert stats["max_price"] == 16950.0
    assert stats["avg_price"] == 16950.0
    assert stats["current_price"] == 16950.0
    assert stats["vs_min_abs"] == 0.0
    assert stats["vs_min_pct"] == 0.0
    assert stats["is_at_historical_low"] is True


def test_statistics_multiple_observations():
    """Verify multi-observation calculations (min, max, avg, deltas)."""
    obs = [
        {"selling_price": 18000.0, "observed_at": "2026-08-01T08:00:00Z"},
        {"selling_price": 15000.0, "observed_at": "2026-09-01T08:00:00Z"}, # Historical low
        {"selling_price": 17000.0, "observed_at": "2026-10-01T08:00:00Z"},
        {"selling_price": 16500.0, "observed_at": "2026-11-14T08:00:00Z"}, # Current
    ]
    stats = compute_statistics(obs)

    assert stats["observation_count"] == 4
    assert stats["has_sufficient_data"] is True
    assert stats["min_price"] == 15000.0
    assert stats["max_price"] == 18000.0
    assert stats["avg_price"] == 16625.0
    assert stats["current_price"] == 16500.0
    # vs min: 16500 - 15000 = +1500, +10%
    assert stats["vs_min_abs"] == 1500.0
    assert stats["vs_min_pct"] == 10.0
    assert stats["is_at_historical_low"] is False


def test_statistics_at_all_time_low():
    """Verify when current price is at or below historical min."""
    obs = [
        {"selling_price": 18000.0, "observed_at": "2026-08-01T08:00:00Z"},
        {"selling_price": 17000.0, "observed_at": "2026-10-01T08:00:00Z"},
        {"selling_price": 16000.0, "observed_at": "2026-11-14T08:00:00Z"}, # Lowest ever!
    ]
    stats = compute_statistics(obs)

    assert stats["current_price"] == 16000.0
    assert stats["min_price"] == 16000.0
    assert stats["vs_min_abs"] == 0.0
    assert stats["vs_min_pct"] == 0.0
    assert stats["is_at_historical_low"] is True


def test_statistics_never_treats_missing_as_zero():
    """Verify None or invalid entries are ignored and never treated as 0."""
    obs = [
        {"selling_price": None, "observed_at": "2026-08-01T08:00:00Z"},
        {"selling_price": 0, "observed_at": "2026-08-02T08:00:00Z"},
        {"selling_price": 16950.0, "observed_at": "2026-11-14T08:00:00Z"},
    ]
    stats = compute_statistics(obs)

    assert stats["observation_count"] == 1
    assert stats["min_price"] == 16950.0
    assert stats["max_price"] == 16950.0
    assert stats["avg_price"] == 16950.0
