"""Statistics service: compute summary statistics over price observations."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_statistics(observations: list[dict]) -> dict:
    """
    Compute summary statistics from a list of price observation dicts.

    Parameters
    ----------
    observations:
        Each dict must have at least ``selling_price`` (float) and
        ``observed_at`` (ISO-8601 string).  ``original_price`` is used
        for reference but not aggregated here.

    Returns
    -------
    dict with keys:
        min_price, max_price, avg_price, current_price,
        vs_min_abs, vs_min_pct,
        observation_count, has_sufficient_data, is_at_historical_low
    """
    # Extract valid (non-None, non-zero) selling prices
    prices: list[float] = []
    for obs in observations:
        sp = obs.get("selling_price")
        if sp is not None and sp > 0:
            prices.append(float(sp))

    count = len(prices)

    if count == 0:
        return {
            "min_price": None,
            "max_price": None,
            "avg_price": None,
            "current_price": None,
            "vs_min_abs": None,
            "vs_min_pct": None,
            "observation_count": 0,
            "has_sufficient_data": False,
            "is_at_historical_low": False,
        }

    min_price = min(prices)
    max_price = max(prices)
    avg_price = round(sum(prices) / count, 2)

    # "current" = the last observation (observations should be sorted ASC)
    current_price = prices[-1]

    vs_min_abs: Optional[float] = None
    vs_min_pct: Optional[float] = None
    is_at_historical_low = False

    if min_price is not None and min_price > 0:
        vs_min_abs = round(current_price - min_price, 2)
        vs_min_pct = round((current_price - min_price) / min_price * 100, 2)
        is_at_historical_low = current_price <= min_price

    has_sufficient_data = count >= 2

    return {
        "min_price": min_price,
        "max_price": max_price,
        "avg_price": avg_price,
        "current_price": current_price,
        "vs_min_abs": vs_min_abs,
        "vs_min_pct": vs_min_pct,
        "observation_count": count,
        "has_sufficient_data": has_sufficient_data,
        "is_at_historical_low": is_at_historical_low,
    }
